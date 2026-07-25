from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout,
)

from workers import PitchWorker, StemWorker, _AUDIO_EXTS


class PitchDialog(QDialog):
    applied = pyqtSignal(str, int)  # (new_file_path, semitones)

    def __init__(self, src: str, current_pitch: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pitch Shift")
        self.setMinimumWidth(280)
        self.setModal(True)
        self._src = src
        self._pitch = current_pitch
        self._worker = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        pitch_row = QHBoxLayout()
        pitch_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(40)
        down_btn.clicked.connect(lambda: self._change(-1))
        pitch_row.addWidget(down_btn)
        self._pitch_lbl = QLabel(self._pitch_str())
        self._pitch_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pitch_lbl.setMinimumWidth(80)
        pitch_row.addWidget(self._pitch_lbl)
        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(40)
        up_btn.clicked.connect(lambda: self._change(+1))
        pitch_row.addWidget(up_btn)
        layout.addLayout(pitch_row)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._apply)
        layout.addWidget(self._apply_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def _pitch_str(self) -> str:
        return f"{self._pitch:+d} st" if self._pitch != 0 else "0 st"

    def _change(self, delta: int):
        self._pitch = max(-12, min(12, self._pitch + delta))
        self._pitch_lbl.setText(self._pitch_str())

    def _apply(self):
        if self._pitch == 0:
            self.applied.emit(self._src, 0)
            self.accept()
            return

        is_audio = Path(self._src).suffix.lower() in _AUDIO_EXTS
        ext = ".wav" if is_audio else Path(self._src).suffix
        out_path = str(
            Path(self._src).parent / f"{Path(self._src).stem}_pitch{self._pitch:+d}{ext}"
        )

        self._apply_btn.setEnabled(False)
        self._progress.setVisible(True)

        self._worker = PitchWorker(self._src, self._pitch, out_path)
        self._worker.progress.connect(self._status.setText)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, path: str):
        self._progress.setVisible(False)
        self.applied.emit(path, self._pitch)
        self.accept()

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._apply_btn.setEnabled(True)
        self._status.setText("")
        QMessageBox.critical(self, "Pitch shift error", msg)


class SplitDialog(QDialog):
    finished_stems = pyqtSignal(dict)

    def __init__(self, src: str, out_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Split Stems")
        self.setMinimumWidth(320)
        self.setModal(True)
        self._src = src
        self._out_dir = out_dir
        self._worker = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        info = QLabel(
            "Separate this track into stems using AI:\n"
            "Vocals · Drums · Bass · Other\n\n"
            "This may take a few minutes."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._apply)
        layout.addWidget(self._apply_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def _apply(self):
        self._apply_btn.setEnabled(False)
        self._progress.setVisible(True)

        self._worker = StemWorker(self._src, self._out_dir)
        self._worker.progress.connect(self._status.setText)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, stems: dict):
        self._progress.setVisible(False)
        self.finished_stems.emit(stems)
        self.accept()

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._apply_btn.setEnabled(True)
        self._status.setText("")
        QMessageBox.critical(self, "Stem separation error", msg)
