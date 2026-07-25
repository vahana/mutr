import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

_AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}


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
