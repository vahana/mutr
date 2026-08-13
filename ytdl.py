#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "yt-dlp>=2024.1.0",
# ]
# ///

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_FORMATS = ("bestvideo[vcodec!^=av01][height<=1080]+bestaudio"
            "/bestvideo[height<=1080]+bestaudio"
            "/best[height<=1080]/best")


def _fetch_title(yt_dlp: str, url: str) -> str:
    try:
        result = subprocess.run(
            [yt_dlp, "--print", "title", "--no-download", url],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip() or "video"
    except Exception:
        return "video"


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
    args = parser.parse_args()

    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        print("yt-dlp not found — install with: brew install yt-dlp", file=sys.stderr)
        return 1

    if args.output:
        out_path = args.output
    else:
        title = _fetch_title(yt_dlp, args.url)
        safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in title).strip()
        out_path = str(Path.home() / "Downloads" / f"{safe}.mkv")

    result = subprocess.run(
        [yt_dlp, args.url,
         "-f", _FORMATS,
         "--merge-output-format", "mkv",
         "--cookies-from-browser", "chrome",
         "-o", out_path],
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
