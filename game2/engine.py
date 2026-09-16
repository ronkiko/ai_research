"""CLI entry point. Component construction and lifecycle belong to GameContainer."""
import argparse

from game import GameContainer
from level import DEFAULT_MAP


def main():
    parser = argparse.ArgumentParser(description='game2 physics laboratory')
    parser.add_argument('--mode', choices=('human', 'mlp'), default='human')
    parser.add_argument('--map', default=str(DEFAULT_MAP), help='Level JSON file')
    parser.add_argument('--port', type=int, default=8765, help='MLP TCP port on 127.0.0.1')
    parser.add_argument('--window', action='store_true', help='Show a spectator window in MLP mode')
    parser.add_argument('--auto', action='store_true', help='Run headless accelerated MLP simulation')
    parser.add_argument('--speed', type=float, default=100,
                        help='Maximum auto wall-clock acceleration (default: 100)')
    args = parser.parse_args()
    if args.auto and args.mode != 'mlp':
        parser.error('--auto is only valid with --mode mlp')
    if args.auto and args.window:
        parser.error('--auto cannot be combined with --window')
    if args.speed <= 0:
        parser.error('--speed must be positive')
    try:
        with GameContainer(args.map, args.mode, port=args.port, window=args.window,
                           auto=args.auto) as game:
            if args.auto:
                game.run_auto(args.speed)
            else:
                game.run()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'game2: {error}\n')


if __name__ == '__main__':
    main()
