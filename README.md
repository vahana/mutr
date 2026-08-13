# mutr

Music practice app for learning and transcribing songs by ear.

## Features

- Load audio/video files as tracks (MP4, MKV, MP3, WAV, FLAC, etc.)
- AI-powered stem separation (vocals, drums, bass, other) via demucs
- Pitch shift individual tracks (±12 semitones) via rubberband
- Loop sections, solo/mute per track, speed control
- Video playback window
- Drag & drop files to add tracks
- Project-based workflow — all files stored in project folder

## Requirements

- Python 3.13
- ffmpeg (for audio extraction and video remuxing)
- rubberband (for pitch shifting)
- demucs (for stem separation, installed on demand via uv)

```bash
brew install ffmpeg rubberband
```

## Usage

```bash
./mutr.py
```

Projects are stored in `~/.mutr/projects/`.

## YouTube downloads

Separate CLI utility:

```bash
./ytdl.py <url> [output_path]
```

Output defaults to `~/Downloads/<title>.mkv`. Requires yt-dlp (`brew install yt-dlp`).

## Shortcuts

| Key | Action |
|-----|--------|
| Space | Play/Pause |
| L | Toggle loop |
| ← → | Seek ±5s |
| ↑ ↓ | Jump between loop markers |
| D | Delete nearest marker |
