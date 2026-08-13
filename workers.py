import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

_AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}


class _Cancelled(Exception):
    pass


class _ProcessWorker(QThread):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._proc = None
        self._stopped = False

    def cancel(self):
        with self._lock:
            self._stopped = True
            proc = self._proc
        if proc is None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _start_proc(self, cmd, **kwargs):
        with self._lock:
            if self._stopped:
                raise _Cancelled()
            self._proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
            return self._proc

    def _finish_proc(self):
        with self._lock:
            self._proc = None
            if self._stopped:
                raise _Cancelled()


class DownloadWorker(_ProcessWorker):
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
            proc = self._start_proc(
                [yt_dlp, self.url,
                 "-f", "bestvideo[vcodec!^=av01][height<=1080]+bestaudio"
                       "/bestvideo[height<=1080]+bestaudio"
                       "/best[height<=1080]/best",
                 "--merge-output-format", "mkv",
                 "--newline",
                 "-o", self.out_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            tail = []
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-20]
                if "[download]" in line or "Merging" in line or "[Merger]" in line:
                    self.progress.emit(line)
            proc.wait()
            self._finish_proc()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(
                    proc.returncode, proc.args,
                    output="\n".join(tail),
                )
            self.finished.emit(self.out_path)
        except _Cancelled:
            pass
        except subprocess.CalledProcessError as e:
            stderr = e.output or ""
            self.error.emit(f"Download failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))



def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"'{tool}' not found — install with: brew install {tool}")
    return path


class PitchWorker(_ProcessWorker):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, src: str, semitones: int, out_path: str):
        super().__init__()
        self.src = src
        self.semitones = semitones
        self.out_path = out_path

    def _run_cmd(self, cmd):
        proc = self._start_proc(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()
        self._finish_proc()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd,
                                                output=stdout, stderr=stderr)

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
                self._run_cmd(
                    [ffmpeg, "-y", "-i", self.src, "-vn",
                     "-ar", "44100", "-ac", "2", "-f", "wav", raw_audio],
                )

                self.progress.emit(f"Shifting pitch {self.semitones:+d} semitones…")
                self._run_cmd(
                    [rubberband, "--pitch", str(self.semitones), raw_audio, shifted],
                )

                if is_audio:
                    shutil.copy2(shifted, self.out_path)
                else:
                    self.progress.emit("Remuxing…")
                    self._run_cmd(
                        [ffmpeg, "-y", "-i", self.src, "-i", shifted,
                         "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                         "-c:a", "aac", "-b:a", "192k", self.out_path],
                    )

            self.finished.emit(self.out_path)
        except _Cancelled:
            pass
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            self.error.emit(f"Command failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))


class StemWorker(_ProcessWorker):
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
            proc = self._start_proc(
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
            self._finish_proc()
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
        except _Cancelled:
            pass
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            self.error.emit(f"Stem separation failed:\n{stderr}")
        except Exception as e:
            self.error.emit(str(e))
