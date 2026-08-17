import array
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSlider,
    QVBoxLayout, QWidget,
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
    clicked = pyqtSignal(float)

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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.width() > 0:
            ratio = max(0.0, min(1.0, event.position().x() / self.width()))
            self.clicked.emit(ratio)
        super().mousePressEvent(event)

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


_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


class _ResizeHandle(QWidget):
    dragged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self._dragging = False
        self._start_y = 0.0

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        mid = self.height() // 2
        p.setPen(QColor(100, 100, 100))
        cx = self.width() // 2
        for i in range(-10, 11, 4):
            p.drawPoint(cx + i, mid)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_y = event.globalPosition().y()

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = int(event.globalPosition().y() - self._start_y)
            self._start_y = event.globalPosition().y()
            self.dragged.emit(delta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False


class TrackRow(QWidget):
    mute_toggled = pyqtSignal(int, bool)
    volume_changed = pyqtSignal(int, float)
    pitch_shift_requested = pyqtSignal(int)
    split_requested = pyqtSignal(int)
    solo_requested = pyqtSignal(int)
    remove_requested = pyqtSignal(int)
    name_changed = pyqtSignal(int, str)
    show_in_finder_requested = pyqtSignal(int)
    show_video_requested = pyqtSignal(int)
    video_resized = pyqtSignal(int)
    seek_requested = pyqtSignal(int, float)

    _ROW_H = 52
    _DEFAULT_VIDEO_H = 480
    _MIN_VIDEO_H = 100

    def __init__(self, track_idx: int, data: TrackData, default_video_height: int = 480, parent=None):
        super().__init__(parent)
        self._idx = track_idx
        self._video_height = default_video_height
        self._video_visible = False

        self._left = QWidget()
        left_layout = QVBoxLayout(self._left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        controls = QWidget()
        controls.setFixedHeight(self._ROW_H)
        self._controls_layout = QHBoxLayout(controls)
        layout = self._controls_layout
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(_ColorSwatch(data.color))

        self._name_lbl = QLabel(data.name)
        self._name_lbl.setFixedWidth(90)
        self._name_lbl.mouseDoubleClickEvent = self._start_rename
        layout.addWidget(self._name_lbl)
        self._name_edit = None

        self._waveform = _WaveformWidget(data.color)
        self._waveform.clicked.connect(lambda ratio: self.seek_requested.emit(self._idx, ratio))
        layout.addWidget(self._waveform, stretch=1)

        left_layout.addWidget(controls)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(self._MIN_VIDEO_H)

        self._resize_handle = _ResizeHandle()
        self._resize_handle.dragged.connect(self._on_resize_dragged)

        leading = 4 + 8 + 6 + 90 + 6  # margin + swatch + spacing + name + spacing

        self._video_container = QWidget()
        self._video_container.setVisible(False)
        vc_layout = QVBoxLayout(self._video_container)
        vc_layout.setContentsMargins(0, 0, 0, 0)
        vc_layout.setSpacing(0)

        video_row = QWidget()
        vr = QHBoxLayout(video_row)
        vr.setContentsMargins(0, 0, 0, 0)
        vr.setSpacing(0)
        self._video_left = QWidget()
        self._video_left.setFixedWidth(leading)
        self._video_right = QWidget()
        self._video_right.setFixedWidth(4)
        vr.addWidget(self._video_left)
        vr.addWidget(self._video_widget, stretch=1)
        vr.addWidget(self._video_right)
        vc_layout.addWidget(video_row)

        handle_row = QWidget()
        hr = QHBoxLayout(handle_row)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(0)
        self._handle_left = QWidget()
        self._handle_left.setFixedWidth(leading)
        self._handle_right = QWidget()
        self._handle_right.setFixedWidth(4)
        hr.addWidget(self._handle_left)
        hr.addWidget(self._resize_handle, stretch=1)
        hr.addWidget(self._handle_right)
        vc_layout.addWidget(handle_row)

        left_layout.addWidget(self._video_container)

        self._left.setFixedHeight(self._ROW_H)

        self._controls_panel = QFrame()
        self._controls_panel.setObjectName("controlsPanel")
        self._controls_panel.setStyleSheet(
            "QFrame#controlsPanel { background: #f7f7f7;"
            " border: 1px solid #c8c8c8; border-radius: 4px; }"
        )
        self._controls_panel.setFixedHeight(self._ROW_H)
        panel_layout = QHBoxLayout(self._controls_panel)
        panel_layout.setContentsMargins(5, 2, 5, 2)
        panel_layout.setSpacing(6)

        is_video = Path(data.file).suffix.lower() in _VIDEO_EXTS
        if is_video:
            self._video_btn = QPushButton("👁")
            self._video_btn.setCheckable(True)
            self._video_btn.setFixedSize(24, 24)
            self._video_btn.setToolTip("Toggle video")
            self._video_btn.toggled.connect(self._on_video_toggled)
            panel_layout.addWidget(self._video_btn)
        else:
            self._video_btn = None

        self._solo_btn = QPushButton("S")
        self._solo_btn.setCheckable(True)
        self._solo_btn.setFixedSize(24, 24)
        self._solo_btn.setToolTip("Solo")
        self._solo_btn.clicked.connect(lambda: self.solo_requested.emit(self._idx))
        panel_layout.addWidget(self._solo_btn)

        self._mute_btn = QPushButton("M")
        self._mute_btn.setCheckable(True)
        self._mute_btn.setChecked(data.muted)
        self._mute_btn.setFixedSize(24, 24)
        self._mute_btn.setToolTip("Mute")
        if data.muted:
            self._mute_btn.setStyleSheet("background: #8b0000; color: white;")
        self._mute_btn.toggled.connect(self._on_mute)
        panel_layout.addWidget(self._mute_btn)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(int(data.volume * 100))
        self._vol_slider.setFixedWidth(60 if is_video else 90)
        self._vol_slider.valueChanged.connect(self._on_volume)
        panel_layout.addWidget(self._vol_slider)

        self._vol_lbl = QLabel(f"{int(data.volume * 100)}%")
        self._vol_lbl.setFixedWidth(36)
        panel_layout.addWidget(self._vol_lbl)

        self._loader = _WaveformLoader(data.file)
        self._loader.ready.connect(self._waveform.set_samples)
        self._loader.start()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def waveform(self) -> _WaveformWidget:
        return self._waveform

    @property
    def left_widget(self) -> QWidget:
        return self._left

    @property
    def controls_panel(self) -> QFrame:
        return self._controls_panel

    @property
    def video_widget(self) -> QVideoWidget:
        return self._video_widget

    def set_video_visible(self, visible: bool):
        if self._video_btn is None:
            return
        self._video_visible = visible
        self._video_container.setVisible(visible)
        self._video_btn.blockSignals(True)
        self._video_btn.setChecked(visible)
        self._video_btn.blockSignals(False)
        if visible:
            total_h = self._ROW_H + self._video_height + self._resize_handle.height()
            self._left.setFixedHeight(total_h)
            self._video_widget.setFixedHeight(self._video_height)
        else:
            self._left.setFixedHeight(self._ROW_H)

    def is_video_visible(self) -> bool:
        return self._video_visible

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
        menu = QMenu(self)
        pitch_act = menu.addAction("Pitch Shift…")
        split_act = menu.addAction("Split Stems…")
        menu.addSeparator()
        finder_act = menu.addAction("Show in Finder")
        menu.addSeparator()
        remove_act = menu.addAction("Remove Track")
        act = menu.exec(event.globalPos())
        if act is None:
            return
        if act == pitch_act:
            self.pitch_shift_requested.emit(self._idx)
        elif act == split_act:
            self.split_requested.emit(self._idx)
        elif act == finder_act:
            self.show_in_finder_requested.emit(self._idx)
        elif act == remove_act:
            self.remove_requested.emit(self._idx)

    # ── rename ───────────────────────────────────────────────────────────────

    def _start_rename(self, event):
        self._name_edit = QLineEdit(self._name_lbl.text(), self)
        self._name_edit.setFixedWidth(90)
        self._name_edit.selectAll()
        self._name_edit.setFocus()
        self._name_edit.returnPressed.connect(self._finish_rename)
        self._name_edit.editingFinished.connect(self._finish_rename)
        self._controls_layout.replaceWidget(self._name_lbl, self._name_edit)
        self._name_lbl.hide()

    def _finish_rename(self):
        if self._name_edit is None:
            return
        new_name = self._name_edit.text().strip()
        if new_name:
            self._name_lbl.setText(new_name)
            self.name_changed.emit(self._idx, new_name)
        self._controls_layout.replaceWidget(self._name_edit, self._name_lbl)
        self._name_edit.deleteLater()
        self._name_edit = None
        self._name_lbl.show()

    # ── private slots ─────────────────────────────────────────────────────────

    def _on_video_toggled(self, checked: bool):
        self.show_video_requested.emit(self._idx)

    def _on_resize_dragged(self, delta: int):
        self._video_height = max(self._MIN_VIDEO_H, self._video_height + delta)
        self._video_widget.setFixedHeight(self._video_height)
        total_h = self._ROW_H + self._video_height + self._resize_handle.height()
        self._left.setFixedHeight(total_h)
        self.video_resized.emit(self._video_height)

    def _on_mute(self, on: bool):
        self._mute_btn.setStyleSheet("background: #8b0000; color: white;" if on else "")
        self.mute_toggled.emit(self._idx, on)

    def _on_volume(self, value: int):
        self._vol_lbl.setText(f"{value}%")
        self.volume_changed.emit(self._idx, value / 100.0)
