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
            self.progress.emit("Downloading…")
            subprocess.run(
                [yt_dlp, self.url,
                 "-f", "bestvideo[vcodec!^=av01][height<=1080]+bestaudio"
                       "/bestvideo[height<=1080]+bestaudio"
                       "/best[height<=1080]/best",
                 "--merge-output-format", "mkv",
                 "-o", self.out_path],
                check=True, capture_output=True,
            )
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

    def __init__(self, src: str, out_dir: str, model: str = "htdemucs", shifts: int = 0):
        super().__init__()
        self.src = src
        self.out_dir = out_dir
        self.model = model
        self.shifts = shifts

    def run(self):
        try:
            uv = _require("uv")
            self.progress.emit("Separating stems…")
            proc = subprocess.Popen(
                [uv, "run", "--with", "demucs", "--with", "numpy",
                 "demucs", "-n", self.model, "--shifts", str(self.shifts),
                 "--out", self.out_dir, self.src],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if "Separating" in line or "%" in line or "track" in line.lower():
                    self.progress.emit(line)
            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, proc.args)

            stems = {}
            for wav in Path(self.out_dir).rglob("*.wav"):
                target = Path(self.out_dir) / wav.name
                if wav.parent != Path(self.out_dir):
                    shutil.move(str(wav), str(target))
                stems[wav.stem] = str(target)

            for d in Path(self.out_dir).iterdir():
                if d.is_dir():
                    shutil.rmtree(d)

            self.finished.emit(stems)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            self.error.emit(f"Stem separation failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))
