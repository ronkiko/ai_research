"""CLI entry point. Component construction and lifecycle belong to GameContainer."""
import argparse

from game import GameContainer
from level import DEFAULT_MAP


def main():
    parser = argparse.ArgumentParser(description='game2 physics laboratory')
    parser.add_argument('--mode', choices=('human', 'agent'), default='human')
    parser.add_argument('--map', default=str(DEFAULT_MAP), help='Level JSON file')
    parser.add_argument('--port', type=int, default=8765, help='Agent TCP port on 127.0.0.1')
    parser.add_argument('--window', action='store_true', help='Show a spectator window in agent mode')
    args = parser.parse_args()
    try:
        with GameContainer(args.map, args.mode, port=args.port, window=args.window) as game:
            game.run()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'game2: {error}\n')


if __name__ == '__main__':
    main()
