"""Client-side defaults for the public GameServer v1 Gateway."""
from __future__ import annotations

from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17600
PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 1.0
DEFAULT_SESSION_FILE = Path(__file__).resolve().parent / "runtime" / "session.json"
