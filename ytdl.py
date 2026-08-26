#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "yt-dlp>=2026.8.19",
# ]
# ///

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


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

    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        print("yt-dlp not found — install with: brew install yt-dlp", file=sys.stderr)
        return 1

    if args.audio_only:
        formats = "bestaudio/best"
    else:
        formats = (f"bestvideo[vcodec!^=av01][height<={args.quality}]+bestaudio"
                   f"/bestvideo[height<={args.quality}]+bestaudio"
                   f"/best[height<={args.quality}]/best")

    if args.output:
        out_path = args.output
    else:
        title = _fetch_title(yt_dlp, args.url)
        safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in title).strip()
        ext = "%(ext)s" if args.audio_only else "mkv"
        out_path = str(Path.home() / "Downloads" / f"{safe}.{ext}")

    cmd = [yt_dlp, args.url,
           "-f", formats,
           "--cookies-from-browser", "chrome",
           "-o", out_path]
    if not args.audio_only:
        cmd += ["--merge-output-format", "mkv"]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
