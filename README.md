# mutr

Music practice app for learning and transcribing songs by ear.

## Features

- Load audio/video files as tracks (MP4, MKV, MP3, WAV, FLAC, etc.)
- Download from YouTube with automatic H.264-compatible format selection
- AI-powered stem separation (vocals, drums, bass, other) via demucs
- Pitch shift individual tracks (±12 semitones) via rubberband
- Loop sections, solo/mute per track, speed control
- Video playback window
- Drag & drop files to add tracks
- Project-based workflow — all files stored in project folder

## Requirements

- Python 3.13
- ffmpeg (for audio extraction and video remuxing)
- yt-dlp (for YouTube downloads)
- rubberband (for pitch shifting)
- demucs (for stem separation, installed on demand via uv)

```bash
brew install ffmpeg rubberband yt-dlp
```

## Usage

```bash
./mutr.py
```

Projects are stored in `~/.mutr/projects/`.

## Shortcuts

| Key | Action |
|-----|--------|
| Space | Play/Pause |
| L | Toggle loop |
| ← → | Seek ±5s |
| ↑ ↓ | Jump between loop markers |
| D | Delete nearest marker |
