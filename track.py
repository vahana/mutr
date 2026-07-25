import array
import subprocess
import threading
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QWidget,
)

_TRACK_COLORS = [
    QColor(60, 110, 170),
    QColor(110, 70, 155),
    QColor(60, 140, 100),
    QColor(155, 105, 45),
    QColor(140, 60, 60),
    QColor(60, 120, 140),
]


def track_color(idx: int) -> QColor:
    return _TRACK_COLORS[idx % len(_TRACK_COLORS)]


@dataclass
class TrackData:
    name: str
    file: str
    source_file: str
    volume: float = 1.0
    muted: bool = False
    pitch_baked: int = 0
    color: QColor = field(default_factory=lambda: QColor(60, 110, 170))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "source_file": self.source_file,
            "volume": self.volume,
            "muted": self.muted,
            "pitch_baked": self.pitch_baked,
        }

    @staticmethod
    def from_dict(d: dict, color: QColor) -> "TrackData":
        return TrackData(
            name=d["name"],
            file=d["file"],
            source_file=d["source_file"],
            volume=d.get("volume", 1.0),
            muted=d.get("muted", False),
            pitch_baked=d.get("pitch_baked", 0),
            color=color,
        )


class _WaveformLoader(QThread):
    ready = pyqtSignal(list)

    _N_SAMPLES = 400
    _RATE = 4000

    def __init__(self, path: str):
        super().__init__()
        self._path = path
        self._lock = threading.Lock()
        self._proc = None
        self._stopped = False

    def stop(self):
        with self._lock:
            self._stopped = True
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def run(self):
        try:
            with self._lock:
                if self._stopped:
                    return
                self._proc = subprocess.Popen(
                    ["ffmpeg", "-i", self._path,
                     "-f", "f32le", "-ac", "1", "-ar", str(self._RATE),
                     "-vn", "pipe:1"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            stdout, _ = self._proc.communicate()
            if self._stopped or self._proc.returncode != 0 or not stdout:
                return
            data = array.array("f", stdout)
            chunk = max(1, len(data) // self._N_SAMPLES)
            peaks = []
            for i in range(0, len(data), chunk):
                block = data[i:i + chunk]
                if block:
                    peaks.append(max(abs(v) for v in block))
            peak = max(peaks) if peaks else 1.0
            if peak > 0:
                peaks = [v / peak for v in peaks]
            self.ready.emit(peaks[:self._N_SAMPLES])
        except Exception:
            pass


class _WaveformWidget(QWidget):
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self._samples: list[float] = []
        self._playhead_ratio: float = 0.0
        self.setMinimumWidth(60)

    def set_samples(self, samples: list[float]):
        self._samples = samples
        self.update()

    def set_playhead_ratio(self, ratio: float):
        self._playhead_ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 18))
        w, h = self.width(), self.height()
        mid = h / 2.0

        if not self._samples:
            p.setPen(QColor(60, 60, 60))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "···")
            return

        color = QColor(self._color)
        color.setAlpha(200)
        p.setPen(color)
        n = len(self._samples)
        for i, amp in enumerate(self._samples):
            x = int(i / n * w)
            half = max(1, int(amp * mid * 0.92))
            p.drawLine(x, int(mid - half), x, int(mid + half))

        ph_x = int(self._playhead_ratio * w)
        p.setPen(QPen(QColor(255, 60, 60), 1))
        p.drawLine(ph_x, 0, ph_x, h)


class _ColorSwatch(QWidget):
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedWidth(8)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), self._color)


class TrackRow(QWidget):
    mute_toggled = pyqtSignal(int, bool)
    volume_changed = pyqtSignal(int, float)
    pitch_shift_requested = pyqtSignal(int)
    split_requested = pyqtSignal(int)
    solo_requested = pyqtSignal(int)

    _ROW_H = 52

    def __init__(self, track_idx: int, data: TrackData, is_source: bool = False, parent=None):
        super().__init__(parent)
        self._idx = track_idx
        self._is_source = is_source
        self.setFixedHeight(self._ROW_H)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(_ColorSwatch(data.color))

        self._name_lbl = QLabel(data.name)
        self._name_lbl.setFixedWidth(90)
        layout.addWidget(self._name_lbl)

        self._waveform = _WaveformWidget(data.color)
        layout.addWidget(self._waveform, stretch=1)

        self._solo_btn = QPushButton("S")
        self._solo_btn.setCheckable(True)
        self._solo_btn.setFixedSize(24, 24)
        self._solo_btn.setToolTip("Solo")
        self._solo_btn.clicked.connect(lambda: self.solo_requested.emit(self._idx))
        layout.addWidget(self._solo_btn)

        self._mute_btn = QPushButton("M")
        self._mute_btn.setCheckable(True)
        self._mute_btn.setChecked(data.muted)
        self._mute_btn.setFixedSize(24, 24)
        self._mute_btn.setToolTip("Mute")
        if data.muted:
            self._mute_btn.setStyleSheet("background: #8b0000; color: white;")
        self._mute_btn.toggled.connect(self._on_mute)
        layout.addWidget(self._mute_btn)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(int(data.volume * 100))
        self._vol_slider.setFixedWidth(90)
        self._vol_slider.valueChanged.connect(self._on_volume)
        layout.addWidget(self._vol_slider)

        self._vol_lbl = QLabel(f"{int(data.volume * 100)}%")
        self._vol_lbl.setFixedWidth(36)
        layout.addWidget(self._vol_lbl)

        self._loader = _WaveformLoader(data.file)
        self._loader.ready.connect(self._waveform.set_samples)
        self._loader.start()

    # ── public API ────────────────────────────────────────────────────────────

    def cleanup(self):
        try:
            self._loader.ready.disconnect()
        except Exception:
            pass
        self._loader.stop()
        self._loader.wait()  # guaranteed fast: proc is killed, run() returns quickly

    def set_playhead_ratio(self, ratio: float):
        self._waveform.set_playhead_ratio(ratio)

    def set_muted(self, on: bool):
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(on)
        self._mute_btn.blockSignals(False)
        self._mute_btn.setStyleSheet("background: #8b0000; color: white;" if on else "")

    def set_solo_active(self, on: bool):
        self._solo_btn.blockSignals(True)
        self._solo_btn.setChecked(on)
        self._solo_btn.blockSignals(False)
        self._solo_btn.setStyleSheet("background: #7a6a00; color: #ffe066;" if on else "")

    # ── context menu ──────────────────────────────────────────────────────────

    def contextMenuEvent(self, event):
        if not self._is_source:
            return
        menu = QMenu(self)
        pitch_act = menu.addAction("Pitch Shift…")
        split_act = menu.addAction("Split Stems…")
        act = menu.exec(event.globalPos())
        if act == pitch_act:
            self.pitch_shift_requested.emit(self._idx)
        elif act == split_act:
            self.split_requested.emit(self._idx)

    # ── private slots ─────────────────────────────────────────────────────────

    def _on_mute(self, on: bool):
        self._mute_btn.setStyleSheet("background: #8b0000; color: white;" if on else "")
        self.mute_toggled.emit(self._idx, on)

    def _on_volume(self, value: int):
        self._vol_lbl.setText(f"{value}%")
        self.volume_changed.emit(self._idx, value / 100.0)
