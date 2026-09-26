"""Small stdlib client for the installed OpenCode HTTP API."""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

# Session permission allowlists, not model instructions. Social state tools are absent.
LEARNING_TOOLS = tuple("learning_v1_" + x for x in (
    "describe", "skills", "training_prepare", "motor_train_start",
    "spine_train_start", "training_status", "training_cancel",
    "verify_start", "verify_status", "verify_cancel", "skill_select",
))

READ_ONLY_LEARNING_TOOLS = (
    "learning_v1_describe",
    "learning_v1_skills",
    "learning_v1_training_status",
    "learning_v1_verify_status",
)

NAVIGATION_TOOLS = (
    "navigation_v1_describe",
    "navigation_v1_observe",
    "navigation_v1_locations",
    "navigation_v1_navigate",
    "navigation_v1_approach",
    "navigation_v1_interact",
    "navigation_v1_action_status",
    "navigation_v1_action_cancel",
)

READ_ONLY_WORLD_TOOLS = (
    "navigation_v1_describe",
    "navigation_v1_observe",
    "navigation_v1_locations",
    *READ_ONLY_LEARNING_TOOLS,
)

TEXT_ONLY_COMPLETION_ATTEMPTS = 3
TEXT_ONLY_RETRY_DELAYS = (0.5, 1.5)

class BackendError(RuntimeError):
    pass


def parse_json(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    elif text.startswith("```\n") and text.endswith("```"):
        text = text[4:-3].strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise BackendError("Модель вернула невалидный JSON") from exc


class OpenCode:
    def __init__(self, url, directory, model=None, variant=None, password=None,
                 log: Callable[[str], None] | None = None):
        self.url = url.rstrip("/")
        self.directory = str(directory)
        self.model = model
        self.variant = variant
        self.password = password
        self.sessions = set()
        self.lock = threading.Lock()
        self.log = log or (lambda _message: None)

    def request(self, method, path, body=None, timeout=240):
        path += ("&" if "?" in path else "?") + urllib.parse.urlencode({"directory": self.directory})
        headers = {"Content-Type": "application/json"}
        if self.password:
            headers["Authorization"] = "Basic " + base64.b64encode(("opencode:" + self.password).encode()).decode()
        req = urllib.request.Request(self.url + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            # Do not expose provider payloads, credentials or internal traces through the UI.
            raise BackendError(f"OpenCode: ошибка {method} {path.split('?')[0]} ({type(exc).__name__})") from exc

    def select_model(self):
        config = self.request("GET", "/config")
        selected = self.model or config.get("model")
        if selected:
            if "/" not in selected:
                raise BackendError("Модель задаётся как provider/model")
            provider, model = selected.split("/", 1)
            self.model = selected
            return {"providerID": provider, "modelID": model}
        raise BackendError("Модель не задана. Укажи --model provider/model; автоматическая замена провайдера запрещена.")

    def create(
        self, title, agent="yuki", parent=None, lab=False, lab_tools=None,
        allowed_tools=None,
    ):
        permissions = [{"permission": "*", "pattern": "*", "action": "deny"}]
        if allowed_tools is not None and (lab or lab_tools is not None):
            raise ValueError("allowed_tools cannot be combined with learning scope")
        allowed = (
            tuple(allowed_tools) if allowed_tools is not None
            else tuple(lab_tools) if lab_tools is not None
            else LEARNING_TOOLS if lab else ()
        )
        permissions.extend({"permission": tool, "pattern": "*", "action": "allow"} for tool in allowed)
        body = {"title": title, "agent": agent, "permission": permissions}
        if parent:
            body["parentID"] = parent
        result = self.request("POST", "/session", body)
        sid = result["id"]
        self.log(f"контекст {agent}: tools={'allowlist' if allowed else 'disabled'}")
        with self.lock:
            self.sessions.add(sid)
        return sid

    def _abort_session(self, sid):
        if not sid:
            return
        try:
            self.request("POST", f"/session/{sid}/abort", {}, timeout=5)
        except BackendError:
            pass

    def complete(
        self, parent, agent, prompt, lab=False, lab_tools=None, allowed_tools=None
    ):
        # Tool-less voices are pure model calls: a transient provider/transport
        # failure can be retried in a fresh child session without duplicating
        # world or learning side effects. Tool-scoped sessions remain strictly
        # single-attempt because an unknown transport outcome must never replay
        # an MCP mutation.
        tool_scoped = bool(lab or lab_tools is not None or allowed_tools is not None)
        attempts = 1 if tool_scoped else TEXT_ONLY_COMPLETION_ATTEMPTS
        model = self.model.split("/", 1)
        body = {
            "agent": agent,
            "model": {"providerID": model[0], "modelID": model[1]},
            "parts": [{"type": "text", "text": prompt}],
        }
        if self.variant:
            body["variant"] = self.variant

        last_error = None
        for attempt in range(1, attempts + 1):
            sid = None
            try:
                sid = self.create(
                    "GameTable " + agent, agent, parent,
                    lab, lab_tools, allowed_tools,
                )
                result = self.request("POST", f"/session/{sid}/message", body)
            except BackendError as exc:
                last_error = exc
                self._abort_session(sid)
                if attempt < attempts:
                    self.log(
                        f"OpenCode {agent}: временный сбой; "
                        f"повтор {attempt + 1}/{attempts}"
                    )
                    time.sleep(TEXT_ONLY_RETRY_DELAYS[attempt - 1])
                    continue
                self.log(f"ошибка ответа {agent}: OpenCode message")
                raise

            if not isinstance(result, dict) or result.get("info", {}).get("error"):
                last_error = BackendError(
                    "OpenCode не завершил ответ модели; проверь серверный журнал"
                )
                self._abort_session(sid)
                if attempt < attempts:
                    self.log(
                        f"OpenCode {agent}: ответ не завершён; "
                        f"повтор {attempt + 1}/{attempts}"
                    )
                    time.sleep(TEXT_ONLY_RETRY_DELAYS[attempt - 1])
                    continue
                raise last_error

            actual = result.get("info", {})
            if actual.get("providerID") != model[0] or actual.get("modelID") != model[1]:
                self._abort_session(sid)
                raise BackendError(
                    "OpenCode вернул ответ другой модели; ход остановлен"
                )

            answer = "\n".join(
                part.get("text", "")
                for part in result.get("parts", [])
                if part.get("type") == "text" and not part.get("ignored")
            )
            if not answer.strip():
                last_error = BackendError("OpenCode вернул пустой ответ")
                self._abort_session(sid)
                if attempt < attempts:
                    self.log(
                        f"OpenCode {agent}: пустой ответ; "
                        f"повтор {attempt + 1}/{attempts}"
                    )
                    time.sleep(TEXT_ONLY_RETRY_DELAYS[attempt - 1])
                    continue
                raise last_error

            trace = []
            if tool_scoped:
                messages = self.request("GET", f"/session/{sid}/message")
                for msg in messages:
                    for part in msg.get("parts", []):
                        if part.get("type") == "tool":
                            trace.append(
                                {"tool": part.get("tool"), "state": part.get("state")}
                            )
                if trace:
                    for item in trace:
                        state = item.get("state") or {}
                        self.log(
                            f"MCP {item.get('tool', '?')}: "
                            f"{state.get('status', 'unknown')}"
                        )
                else:
                    self.log("MCP: лабораторный контекст не вызвал инструментов")
            return {"session_id": sid, "text": answer, "tools": trace}

        raise last_error or BackendError("OpenCode не завершил ответ модели")

    def close_sessions(self):
        # Owned sessions only; no user TUI history is touched here.
        with self.lock:
            sessions = list(self.sessions)
            self.sessions.clear()
        for sid in sessions:
            try:
                self.request("DELETE", f"/session/{sid}", timeout=5)
            except BackendError:
                pass
