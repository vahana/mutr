import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
)

from workers import DownloadWorker, PitchWorker, StemWorker, _AUDIO_EXTS


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


_MODELS = [
    ("htdemucs",      "Demucs (4 stems, default)"),
    ("htdemucs_ft",   "Demucs Fine-Tuned (4 stems, higher quality)"),
    ("htdemucs_6s",   "Demucs 6-Stem (vocals, drums, bass, guitar, piano, other)"),
    ("mdx_extra_q",   "MDX Extra (best vocal separation)"),
]


class SplitDialog(QDialog):
    finished_stems = pyqtSignal(dict)

    def __init__(self, src: str, out_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Split Stems")
        self.setMinimumWidth(420)
        self.resize(560, 400)
        self.setModal(True)
        self._src = src
        self._out_dir = out_dir
        self._worker = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        info = QLabel("Separate audio into individual stems")
        info.setWordWrap(True)
        layout.addWidget(info)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._model_cb = QComboBox()
        self._model_cb.addItems([label for _, label in _MODELS])
        self._model_cb.setCurrentIndex(2)
        model_row.addWidget(self._model_cb, stretch=1)
        layout.addLayout(model_row)

        shift_row = QHBoxLayout()
        shift_row.addWidget(QLabel("Quality:"))
        self._shifts_sb = QSpinBox()
        self._shifts_sb.setRange(0, 20)
        self._shifts_sb.setValue(10)
        self._shifts_sb.setToolTip("Higher = better separation quality but slower. 0 = fast, 10 = paper default.")
        shift_row.addWidget(self._shifts_sb)
        shift_row.addStretch()
        layout.addLayout(shift_row)

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

        model = _MODELS[self._model_cb.currentIndex()][0]
        shifts = self._shifts_sb.value()
        self._worker = StemWorker(self._src, self._out_dir, model=model, shifts=shifts)
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


class DownloadDialog(QDialog):
    file_ready = pyqtSignal(str)

    def __init__(self, start_dir: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download from YouTube")
        self.setMinimumWidth(460)
        self._start_dir = start_dir or str(Path.home() / "Downloads")
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("YouTube URL")
        layout.addWidget(self._url_edit)

        self._btn = QPushButton("Download")
        self._btn.clicked.connect(self._on_go)
        layout.addWidget(self._btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def _on_go(self):
        url = self._url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "mutr", "Paste a YouTube URL first.")
            return

        self._status.setText("Fetching title…")
        QApplication.processEvents()
        try:
            yt_dlp = shutil.which("yt-dlp")
            if yt_dlp:
                result = subprocess.run(
                    [yt_dlp, "--print", "title", "--no-download", url],
                    capture_output=True, text=True, timeout=15,
                )
                raw_title = result.stdout.strip() or "video"
            else:
                raw_title = "video"
        except Exception:
            raw_title = "video"
        self._status.setText("")

        safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in raw_title).strip()
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save download", str(Path(self._start_dir) / f"{safe}.mkv"),
            "Video (*.mkv)",
        )
        if not out_path:
            return

        self._btn.setEnabled(False)
        self._progress.setVisible(True)

        self._worker = DownloadWorker(url, out_path)
        self._worker.progress.connect(self._status.setText)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, path: str):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._status.setText("Done.")
        self.file_ready.emit(path)
        self.accept()

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._status.setText("")
        QMessageBox.critical(self, "Download error", msg)
