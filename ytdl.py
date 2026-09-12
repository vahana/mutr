#!/usr/bin/env -S uv run

import argparse
import sys
from pathlib import Path

from mutr_core.ytdl import download


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ytdl",
        description="Download a YouTube video as mkv",
    )
    parser.add_argument("url")
    parser.add_argument(
        "output", nargs="?",
        help="output path (default: ~/Downloads/<title>.mkv)",
    )
    parser.add_argument(
        "-q", "--quality", type=int,
        choices=[144, 240, 360, 480, 720, 1080, 1440, 2160],
        default=1080,
        help="max video height (default: 1080)",
    )
    parser.add_argument(
        "-a", "--audio-only", action="store_true",
        help="download audio only",
    )
    args = parser.parse_args()

    if args.output:
        out_dir = Path(args.output).parent or Path(".")
        filename = Path(args.output).name
    else:
        out_dir = Path.home() / "Downloads"
        filename = None

    try:
        download(args.url, out_dir, quality=args.quality,
                 audio_only=args.audio_only, on_line=print, filename=filename)
        return 0
    except Exception as e:
        print(f"ytdl failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
