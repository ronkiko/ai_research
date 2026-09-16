"""Process entrypoint for the authoritative Console Engine."""
from __future__ import annotations

import argparse

from ..config import EngineManifest, SessionConfig
from .engine import Engine, EngineService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 authoritative Engine")
    parser.add_argument("--config", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    config = SessionConfig.from_file(args.config)
    manifest = EngineManifest.from_file(args.manifest)
    service = EngineService(Engine.from_config(config, args.config, manifest.session_id), manifest, config)
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
