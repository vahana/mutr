import subprocess
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from mutr_core.pitch import pitch_shift
from mutr_core.process import CancelledError
from mutr_core.stems import split_stems


class PitchWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, src: str, semitones: int, out_path: str):
        super().__init__()
        self._src = src
        self._semitones = semitones
        self._out_path = out_path
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            pitch_shift(self._src, self._out_path, self._semitones,
                        on_line=self.progress.emit, cancel=self._cancel)
            self.finished.emit(self._out_path)
        except CancelledError:
            pass
        except subprocess.CalledProcessError as e:
            self.error.emit(f"Command failed:\n{e}")
        except Exception as e:
            self.error.emit(str(e))


class StemWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, src: str, out_dir: str, model: str = "htdemucs", shifts: int = 0):
        super().__init__()
        self._src = src
        self._out_dir = out_dir
        self._model = model
        self._shifts = shifts
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            stems = split_stems(self._src, self._out_dir, model=self._model,
                                shifts=self._shifts, on_line=self.progress.emit,
                                cancel=self._cancel)
            self.finished.emit(stems)
        except CancelledError:
            pass
        except subprocess.CalledProcessError:
            self.error.emit("Stem separation failed.")
        except Exception as e:
            self.error.emit(str(e))
