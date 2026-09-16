"""Process entrypoint for the Console Controller."""
from __future__ import annotations

import argparse

from ..config import ControllerManifest
from .controller import ControllerService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Controller subsystem")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--hold-ticks", type=int, default=1)
    parser.add_argument("--lead-ticks", type=int, default=4)
    args = parser.parse_args(argv)
    service = ControllerService(ControllerManifest.from_file(args.manifest),
                                args.hold_ticks, args.lead_ticks)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
