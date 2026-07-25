import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

_AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}


class DownloadWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, out_path: str):
        super().__init__()
        self.url = url
        self.out_path = out_path

    def run(self):
        try:
            yt_dlp = _require("yt-dlp")
            ffmpeg = _require("ffmpeg")

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                raw = str(tmp_path / "raw.%(ext)s")

                self.progress.emit("Downloading…")
                subprocess.run(
                    [yt_dlp, self.url,
                     "-f", "bestvideo+bestaudio/best",
                     "--merge-output-format", "mkv",
                     "-o", raw],
                    check=True, capture_output=True,
                )

                downloaded = next(tmp_path.glob("raw.*"))

                ffprobe = shutil.which("ffprobe") or ffmpeg
                probe = subprocess.run(
                    [ffprobe, "-v", "quiet", "-select_streams", "v:0",
                     "-show_entries", "stream=codec_name",
                     "-of", "csv=p=0", str(downloaded)],
                    capture_output=True, text=True,
                )
                vcodec = probe.stdout.strip()

                if vcodec == "h264":
                    self.progress.emit("Remuxing…")
                    subprocess.run(
                        [ffmpeg, "-y", "-i", str(downloaded),
                         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         self.out_path],
                        check=True, capture_output=True,
                    )
                else:
                    dur = subprocess.run(
                        [ffprobe, "-v", "quiet", "-show_entries",
                         "format=duration", "-of", "csv=p=0", str(downloaded)],
                        capture_output=True, text=True,
                    )
                    try:
                        total_sec = float(dur.stdout.strip())
                    except (ValueError, AttributeError):
                        total_sec = 0.0

                    self.progress.emit("Converting to H.264… 0%")
                    proc = subprocess.Popen(
                        [ffmpeg, "-y", "-i", str(downloaded),
                         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                         "-c:a", "aac", "-b:a", "192k",
                         "-progress", "pipe:1", "-nostats",
                         self.out_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    for line in proc.stdout:
                        if line.startswith("out_time_ms=") and total_sec > 0:
                            try:
                                ms = int(line.split("=")[1])
                                pct = min(99, int(ms / 1_000_000 / total_sec * 100))
                                self.progress.emit(f"Converting to H.264… {pct}%")
                            except (ValueError, IndexError):
                                pass
                    proc.wait()
                    if proc.returncode != 0:
                        raise subprocess.CalledProcessError(proc.returncode, [ffmpeg])

            self.finished.emit(self.out_path)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            self.error.emit(f"Download failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))



def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"'{tool}' not found — install with: brew install {tool}")
    return path


class PitchWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, src: str, semitones: int, out_path: str):
        super().__init__()
        self.src = src
        self.semitones = semitones
        self.out_path = out_path

    def run(self):
        try:
            rubberband = _require("rubberband")
            ffmpeg = _require("ffmpeg")
            is_audio = Path(self.src).suffix.lower() in _AUDIO_EXTS

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                raw_audio = str(tmp_path / "audio.wav")
                shifted = str(tmp_path / "shifted.wav")

                self.progress.emit("Extracting audio…")
                subprocess.run(
                    [ffmpeg, "-y", "-i", self.src, "-vn",
                     "-ar", "44100", "-ac", "2", "-f", "wav", raw_audio],
                    check=True, capture_output=True,
                )

                self.progress.emit(f"Shifting pitch {self.semitones:+d} semitones…")
                subprocess.run(
                    [rubberband, "--pitch", str(self.semitones), raw_audio, shifted],
                    check=True, capture_output=True,
                )

                if is_audio:
                    import shutil as _sh
                    _sh.copy2(shifted, self.out_path)
                else:
                    self.progress.emit("Remuxing…")
                    subprocess.run(
                        [ffmpeg, "-y", "-i", self.src, "-i", shifted,
                         "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                         "-c:a", "aac", "-b:a", "192k", self.out_path],
                        check=True, capture_output=True,
                    )

            self.finished.emit(self.out_path)

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            self.error.emit(f"Command failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))


class StemWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, src: str, out_dir: str):
        super().__init__()
        self.src = src
        self.out_dir = out_dir

    def run(self):
        try:
            uv = _require("uv")
            self.progress.emit("Separating stems (this may take a few minutes)…")
            subprocess.run(
                [uv, "run", "--with", "demucs", "--with", "numpy",
                 "demucs", "--out", self.out_dir, self.src],
                check=True, capture_output=True,
            )

            stems = {}
            for wav in Path(self.out_dir).rglob("*.wav"):
                stems[wav.stem] = str(wav)

            self.finished.emit(stems)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            self.error.emit(f"Stem separation failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))
