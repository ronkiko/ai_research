"""Process entrypoint for the Console Controller."""
from __future__ import annotations

import argparse

from ..config import ControllerManifest
from .controller import ControllerService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Controller subsystem")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    service = ControllerService(ControllerManifest.from_file(args.manifest))
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
