from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from typing import Any

from app.application import GameApplication


def to_jsonable(value: Any):
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


class ApiRouter:
    def __init__(self, app: GameApplication):
        self._app = app
        self._lock = threading.Lock()
        self._routes = {
            ("GET", "/api/state"): self._state,
            ("GET", "/api/games"): self._games,
            ("POST", "/api/games/select"): self._games_select,
            ("GET", "/api/models"): self._models,
            ("POST", "/api/models/select"): self._models_select,
            ("POST", "/api/models/reset"): self._models_reset,
            ("GET", "/api/modes"): self._modes,
            ("POST", "/api/modes/select"): self._modes_select,
            ("POST", "/api/run/start"): self._run_start,
            ("POST", "/api/run/stop"): self._run_stop,
            ("POST", "/api/run/tick"): self._run_tick,
            ("POST", "/api/run/steps"): self._run_steps,
            ("GET", "/api/snapshots"): self._snapshots,
            ("POST", "/api/snapshots/save"): self._snapshots_save,
            ("GET", "/api/snapshots/read"): self._snapshots_read,
            ("GET", "/api/lab/engines"): self._lab_engines,
            ("GET", "/api/lab/report"): self._lab_report,
            ("POST", "/api/lab/report-current"): self._lab_report_current,
        }

    def dispatch(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        raw_body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        handler = self._routes.get((method, path))
        if handler is None:
            if any(route_path == path for _, route_path in self._routes):
                return self._error(405, "method_not_allowed", f"Method {method} is not allowed for {path}")
            return self._error(404, "not_found", f"Unknown API endpoint: {path}")

        try:
            body = self._decode_json_body(raw_body) if method == "POST" else {}
            with self._lock:
                data, message = handler(query, body)
            return self._ok(data, message)
        except ApiError as exc:
            return self._error(exc.status, exc.error, exc.message)
        except Exception as exc:
            return self._error(500, "internal_error", str(exc))

    def _state(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"state": self._app.state()}, ""

    def _games(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"games": self._app.list_games()}, ""

    def _games_select(self, _query: dict[str, list[str]], body: dict[str, Any]):
        key = self._require_string(body, "key")
        result = self._app.select_game(key)
        if not result.ok:
            raise ApiError(400, "bad_request", result.message or f"Could not select game: {key}")
        return {"result": result, "state": self._app.state()}, result.message

    def _models(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"models": self._app.list_models()}, ""

    def _models_select(self, _query: dict[str, list[str]], body: dict[str, Any]):
        key = self._require_string(body, "key")
        result = self._app.select_model(key)
        if not result.ok:
            raise ApiError(400, "bad_request", result.message or f"Could not select model: {key}")
        return {"result": result, "state": self._app.state()}, result.message

    def _models_reset(self, _query: dict[str, list[str]], body: dict[str, Any]):
        key = self._require_string(body, "key")
        result = self._app.reset_model(key)
        if not result.ok:
            raise ApiError(400, "bad_request", result.message or f"Could not reset model: {key}")
        return {"result": result, "state": self._app.state()}, result.message

    def _modes(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"modes": self._app.list_modes()}, ""

    def _modes_select(self, _query: dict[str, list[str]], body: dict[str, Any]):
        key = self._require_string(body, "key")
        result = self._app.set_mode(key)
        if not result.ok:
            raise ApiError(400, "bad_request", result.message or f"Could not select mode: {key}")
        return {"result": result, "state": self._app.state()}, result.message

    def _run_start(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"run": self._app.start_run()}, "Run started."

    def _run_stop(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"run": self._app.stop_run()}, "Run stopped."

    def _run_tick(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"run": self._app.tick()}, ""

    def _run_steps(self, _query: dict[str, list[str]], body: dict[str, Any]):
        raw_steps = body.get("steps")
        if raw_steps is None:
            raise ApiError(400, "bad_request", "Field 'steps' is required.")
        try:
            steps = int(raw_steps)
        except (TypeError, ValueError):
            raise ApiError(400, "bad_request", "Field 'steps' must be an integer.")
        return {"run": self._app.run_steps(steps)}, f"Ran {max(1, min(steps, 100000))} steps."

    def _snapshots(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"snapshots": self._app.list_snapshots()}, ""

    def _snapshots_save(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        snapshot = self._app.save_snapshot()
        if snapshot is None:
            raise ApiError(409, "snapshot_unavailable", "Could not build snapshot from the current state.")
        return {"snapshot": snapshot}, f"Snapshot saved: {snapshot.id}"

    def _snapshots_read(self, query: dict[str, list[str]], _body: dict[str, Any]):
        snapshot_id = self._require_query_value(query, "id")
        snapshot = self._app.read_snapshot(snapshot_id)
        if snapshot is None:
            raise ApiError(404, "not_found", f"Snapshot not found: {snapshot_id}")
        return {"snapshot": snapshot}, ""

    def _lab_engines(self, _query: dict[str, list[str]], _body: dict[str, Any]):
        return {"engines": self._app.list_lab_engines()}, ""

    def _lab_report(self, query: dict[str, list[str]], _body: dict[str, Any]):
        snapshot_id = self._require_query_value(query, "snapshot")
        engine_key = self._require_query_value(query, "engine")
        self._require_engine(engine_key)
        snapshot = self._app.read_snapshot(snapshot_id)
        if snapshot is None:
            raise ApiError(404, "not_found", f"Snapshot not found: {snapshot_id}")
        report = self._app.render_report(snapshot_id, engine_key)
        return {"report": report}, ""

    def _lab_report_current(self, _query: dict[str, list[str]], body: dict[str, Any]):
        engine_key = self._require_string(body, "engine")
        self._require_engine(engine_key)
        snapshot = self._app.build_snapshot()
        if snapshot is None:
            raise ApiError(409, "snapshot_unavailable", "Could not build snapshot from the current state.")
        report = self._app.render_report_from_body(snapshot.model, snapshot.body, engine_key)
        return {"snapshot": snapshot, "report": report}, ""

    def _require_engine(self, key: str) -> None:
        if any(engine.key == key for engine in self._app.list_lab_engines()):
            return
        raise ApiError(404, "not_found", f"Unknown lab engine: {key}")

    @staticmethod
    def _decode_json_body(raw_body: bytes) -> dict[str, Any]:
        if not raw_body:
            return {}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except UnicodeDecodeError:
            raise ApiError(400, "bad_request", "Request body must be valid UTF-8 JSON.")
        except json.JSONDecodeError:
            raise ApiError(400, "bad_request", "Request body must be valid JSON.")
        if not isinstance(payload, dict):
            raise ApiError(400, "bad_request", "JSON body must be an object.")
        return payload

    @staticmethod
    def _require_string(body: dict[str, Any], field: str) -> str:
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ApiError(400, "bad_request", f"Field '{field}' must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _require_query_value(query: dict[str, list[str]], field: str) -> str:
        values = query.get(field, [])
        if not values or not values[0].strip():
            raise ApiError(400, "bad_request", f"Query parameter '{field}' is required.")
        return values[0].strip()

    @staticmethod
    def _ok(data: dict[str, Any], message: str) -> tuple[int, dict[str, Any]]:
        return 200, {
            "ok": True,
            "data": to_jsonable(data),
            "message": message,
        }

    @staticmethod
    def _error(status: int, error: str, message: str) -> tuple[int, dict[str, Any]]:
        return status, {
            "ok": False,
            "error": error,
            "message": message,
        }

