import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Smash Replay Recorder')
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'SmashReplayRecorder')
    parser.add_argument('--admin-helper', nargs=3, metavar=('PIPE', 'KEY', 'PARENT_PID'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.admin_helper:
        from recorder_app.admin import serve
        pipe, key, parent = args.admin_helper
        serve(pipe, key, int(parent))
        return
    from recorder_app.ui import run
    run(args.state_dir)


if __name__ == '__main__':
    main()
