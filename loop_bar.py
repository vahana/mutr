from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QMenu, QSlider, QStyle, QToolTip, QWidget


def _ms_to_str(ms: float) -> str:
    s = int(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


class SeekSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_pos(event)
            self.sliderPressed.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_pos(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.sliderReleased.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _apply_pos(self, event):
        val = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            event.position().toPoint().x(), self.width(),
        )
        self.setValue(val)


class LoopBar(QWidget):
    seek_requested = pyqtSignal(float)
    markers_changed = pyqtSignal(list)
    segment_selected = pyqtSignal(int)

    _SEG_COLORS = [
        (QColor(35, 70, 110), QColor(60, 110, 170)),
        (QColor(70, 40, 100), QColor(110, 70, 155)),
        (QColor(35, 90, 65), QColor(60, 140, 100)),
        (QColor(100, 65, 25), QColor(155, 105, 45)),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self._total_ms = 0.0
        self._markers: list[float] = []
        self._active_segment = -1
        self._playhead_ms = 0.0
        self._drag_idx = -1
        self._loop_active = False
        self.setMouseTracking(True)

    def set_loop_active(self, on: bool):
        self._loop_active = on
        self.update()

    def set_total(self, ms: float):
        self._total_ms = ms
        self.update()

    def set_playhead(self, ms: float):
        self._playhead_ms = ms
        self.update()

    def set_markers(self, markers: list[float]):
        self._markers = sorted(markers)
        self.update()

    def set_active_segment(self, idx: int):
        self._active_segment = idx
        self.update()

    def get_segment_bounds(self, idx: int) -> tuple[float, float] | None:
        if idx < 0:
            return None
        all_m = [0.0] + self._markers + [self._total_ms]
        if idx >= len(all_m) - 1:
            return None
        return all_m[idx], all_m[idx + 1]

    def _snap(self, ms: float) -> float:
        return round(ms / 1000.0) * 1000.0

    def _ms_to_x(self, ms: float) -> float:
        if self._total_ms <= 0:
            return 0.0
        return ms / self._total_ms * self.width()

    def _x_to_ms(self, x: float) -> float:
        if self._total_ms <= 0:
            return 0.0
        return max(0.0, min(self._total_ms, x / self.width() * self._total_ms))

    def _marker_near(self, x: float) -> int:
        for i, m in enumerate(self._markers):
            if abs(self._ms_to_x(m) - x) <= 12:
                return i
        return -1

    def _segment_at_x(self, x: float) -> int:
        ms = self._x_to_ms(x)
        all_m = [0.0] + self._markers + [self._total_ms]
        for i in range(len(all_m) - 1):
            if all_m[i] <= ms < all_m[i + 1]:
                return i
        return max(0, len(all_m) - 2)

    def mousePressEvent(self, event):
        if self._total_ms <= 0:
            return
        x = event.position().x()
        i = self._marker_near(x)
        if i >= 0:
            self._drag_idx = i
            return
        if event.button() == Qt.MouseButton.LeftButton:
            seg = self._segment_at_x(x)
            self._active_segment = seg
            all_m = [0.0] + self._markers + [self._total_ms]
            self.seek_requested.emit(all_m[seg])
            self.segment_selected.emit(seg)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if self._total_ms <= 0 or self._drag_idx >= 0:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            i = self._marker_near(x)
            if i >= 0:
                self._markers.pop(i)
                n_segs = len(self._markers) + 1
                if self._active_segment >= n_segs:
                    self._active_segment = n_segs - 1
                self.markers_changed.emit(list(self._markers))
                self.update()
            else:
                ms = self._snap(self._x_to_ms(x))
                if ms == 0.0 or ms in self._markers or ms >= self._total_ms:
                    return
                self._markers.append(ms)
                self._markers.sort()
                self.markers_changed.emit(list(self._markers))
                self.update()

    def mouseMoveEvent(self, event):
        if self._total_ms > 0:
            ms = self._x_to_ms(event.position().x())
            QToolTip.showText(event.globalPosition().toPoint(), _ms_to_str(ms), self)
        if self._drag_idx >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            i = self._drag_idx
            lo = self._markers[i - 1] if i > 0 else 0.0
            hi = self._markers[i + 1] if i < len(self._markers) - 1 else self._total_ms
            ms = self._snap(self._x_to_ms(event.position().x()))
            ms = max(lo + 1000.0, min(hi - 1000.0, ms))
            if ms != self._markers[i]:
                self._markers[i] = ms
                self.markers_changed.emit(list(self._markers))
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_idx = -1

    def contextMenuEvent(self, event):
        if self._total_ms <= 0:
            return
        i = self._marker_near(event.pos().x())
        if i < 0:
            return
        menu = QMenu(self)
        act = menu.addAction("Remove marker")
        if menu.exec(event.globalPosition().toPoint()) == act:
            self._markers.pop(i)
            n_segs = len(self._markers) + 1
            if self._active_segment >= n_segs:
                self._active_segment = n_segs - 1
            self.markers_changed.emit(list(self._markers))
            self.update()

    def paintEvent(self, _event):
        if self._total_ms <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        all_m = [0.0] + self._markers + [self._total_ms]

        p.fillRect(0, 0, w, h, QColor(28, 28, 28))

        for i in range(len(all_m) - 1):
            x0 = int(self._ms_to_x(all_m[i]))
            x1 = int(self._ms_to_x(all_m[i + 1]))
            inactive, active = self._SEG_COLORS[i % len(self._SEG_COLORS)]
            p.fillRect(x0 + 1, 1, x1 - x0 - 1, h - 2,
                       active if i == self._active_segment else inactive)

        if not self._loop_active:
            p.fillRect(0, 1, w, h - 2, QColor(0, 0, 0, 110))

        p.setPen(QPen(QColor(200, 200, 200, 200), 1))
        for m in self._markers:
            x = int(self._ms_to_x(m))
            p.drawLine(x, 0, x, h)

        ph_x = int(self._ms_to_x(self._playhead_ms))
        p.setPen(QPen(QColor(255, 60, 60), 1))
        p.drawLine(ph_x, 0, ph_x, h)
