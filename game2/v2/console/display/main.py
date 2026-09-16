"""Process entrypoint for the Console Display."""
from __future__ import annotations

import argparse

from ..config import DisplayManifest
from .display import DisplayService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Display subsystem")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    return DisplayService(DisplayManifest.from_file(args.manifest)).run()


if __name__ == "__main__":
    raise SystemExit(main())
