"""GameLab-owned extra GameClient Host instances."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from gameclient.v1.clients.base import HostClient as BaseHostClient, HostClientError

from .config import (
    DEFAULT_HOST_ID,
    HOST_BIND,
    HOST_PORT,
    HOST_TIMEOUT,
    LAB_HOST_PORT_END,
    LAB_HOST_PORT_START,
)


HOST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class LabHostError(RuntimeError):
    code = "HOST_ERROR"


class LabHostNotFound(LabHostError):
    code = "HOST_NOT_FOUND"


class LabHostNotRunning(LabHostError):
    code = "HOST_NOT_RUNNING"


class LabHostExists(LabHostError):
    code = "HOST_EXISTS"


class LabHostPermissionDenied(LabHostError):
    code = "PERMISSION_DENIED"


class LabHostCatalog:
    def __init__(self, registry_path: Path | None = None) -> None:
        configured = os.environ.get("ORGANISM_HOST_REGISTRY") or os.environ.get("GAMELAB_HOST_REGISTRY")
        self.registry_path = (
            Path(registry_path)
            if registry_path is not None
            else Path(configured)
            if configured
            else Path(__file__).resolve().parent / "runtime" / "hosts.json"
        )
        self.log_dir = self.registry_path.parent / "hosts"
        self._lock = threading.RLock()

    @staticmethod
    def _validate(host_id: str) -> str:
        if not isinstance(host_id, str) or not HOST_ID_RE.fullmatch(host_id):
            raise ValueError(
                "host_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
            )
        return host_id

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        hosts = payload.get("hosts")
        return hosts if isinstance(hosts, dict) else {}

    def _save(self, hosts: dict[str, dict[str, Any]]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.registry_path.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"version": 1, "hosts": hosts}, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.registry_path)

    @staticmethod
    def _proc_start_time(pid: int) -> str | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            return stat.split(") ", 1)[1].split()[19]
        except (OSError, IndexError):
            return None

    def _alive(self, record: dict[str, Any]) -> bool:
        pid = record.get("pid")
        start_time = record.get("start_time")
        if type(pid) is not int or not isinstance(start_time, str):
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return self._proc_start_time(pid) == start_time

    @staticmethod
    def _probe(port: int) -> dict[str, Any] | None:
        client = BaseHostClient(
            "gamelab-host-probe",
            host=HOST_BIND,
            port=port,
            timeout=0.2,
        )
        try:
            response = client.health()
            return response if response.get("component") == "gameclient_host" else None
        except HostClientError:
            return None
        finally:
            client.close()

    @staticmethod
    def _port_available(port: int) -> bool:
        sock = socket.socket()
        try:
            sock.bind((HOST_BIND, port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    @staticmethod
    def _public(
        host_id: str,
        port: int,
        owner: str,
        status: str,
        health: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "host_id": host_id,
            "host": HOST_BIND,
            "port": port,
            "owner": owner,
            "status": status,
            "player_id": health.get("player_id") if health else None,
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            hosts = self._load()
            default_health = self._probe(HOST_PORT)
            result = [
                self._public(
                    DEFAULT_HOST_ID,
                    HOST_PORT,
                    "game_v1",
                    "running" if default_health else "stopped",
                    default_health,
                )
            ]
            for host_id in sorted(hosts):
                record = hosts[host_id]
                port = int(record["port"])
                health = self._probe(port) if self._alive(record) else None
                result.append(
                    self._public(
                        host_id,
                        port,
                        "gamelab_v1",
                        "running" if health else "stopped",
                        health,
                    )
                )
            return result

    def resolve(self, host_id: str) -> tuple[str, int]:
        host_id = self._validate(host_id)
        if host_id == DEFAULT_HOST_ID:
            if self._probe(HOST_PORT) is None:
                raise LabHostNotRunning(
                    f"Host {DEFAULT_HOST_ID!r} is not running"
                )
            return HOST_BIND, HOST_PORT

        with self._lock:
            record = self._load().get(host_id)
            if record is None:
                raise LabHostNotFound(f"Host {host_id!r} does not exist")
            port = int(record["port"])
            if not self._alive(record) or self._probe(port) is None:
                raise LabHostNotRunning(f"Host {host_id!r} is not running")
            return HOST_BIND, port

    def create(self, host_id: str) -> dict[str, Any]:
        host_id = self._validate(host_id)
        if host_id == DEFAULT_HOST_ID:
            raise LabHostExists(f"Host {host_id!r} already exists")

        with self._lock:
            hosts = self._load()
            if host_id in hosts:
                raise LabHostExists(f"Host {host_id!r} already exists")

            used = {int(item["port"]) for item in hosts.values()}
            port = next(
                (
                    candidate
                    for candidate in range(LAB_HOST_PORT_START, LAB_HOST_PORT_END + 1)
                    if candidate not in used and self._port_available(candidate)
                ),
                None,
            )
            if port is None:
                raise LabHostError("no free laboratory Host ports")

            self.log_dir.mkdir(parents=True, exist_ok=True)
            with (self.log_dir / f"{host_id}.log").open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "gameclient.v1.host.server",
                        "--host",
                        HOST_BIND,
                        "--port",
                        str(port),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

            record = {
                "port": port,
                "pid": process.pid,
                "start_time": self._proc_start_time(process.pid),
            }
            hosts[host_id] = record
            self._save(hosts)

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    hosts.pop(host_id, None)
                    self._save(hosts)
                    raise LabHostError(f"Host {host_id!r} exited during startup")
                health = self._probe(port)
                if health is not None:
                    return self._public(
                        host_id, port, "gamelab_v1", "running", health
                    )
                time.sleep(0.05)

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
            hosts.pop(host_id, None)
            self._save(hosts)
            raise LabHostError(f"Host {host_id!r} did not become ready")

    def delete(self, host_id: str) -> dict[str, Any]:
        host_id = self._validate(host_id)
        if host_id == DEFAULT_HOST_ID:
            raise LabHostPermissionDenied(
                f"Host {DEFAULT_HOST_ID!r} belongs to game_v1"
            )

        with self._lock:
            hosts = self._load()
            record = hosts.get(host_id)
            if record is None:
                raise LabHostNotFound(f"Host {host_id!r} does not exist")

            port = int(record["port"])
            if self._alive(record):
                client = BaseHostClient(
                    "gamelab-host-delete",
                    host=HOST_BIND,
                    port=port,
                    timeout=HOST_TIMEOUT,
                )
                try:
                    health = client.health()
                    if health.get("logged_in"):
                        try:
                            client.logout()
                        except HostClientError:
                            pass
                except HostClientError:
                    pass
                finally:
                    client.close()

                pid = int(record["pid"])
                try:
                    os.killpg(pid, signal.SIGTERM)
                except OSError:
                    pass
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if not self._alive(record):
                        break
                    time.sleep(0.05)
                if self._alive(record):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass

            hosts.pop(host_id, None)
            self._save(hosts)
            return {"deleted": True, "host_id": host_id}


host_catalog = LabHostCatalog()
