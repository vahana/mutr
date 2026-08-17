#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "PyQt6>=6.6.0",
# ]
# ///

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF, QSize, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSlider, QSizePolicy, QSplitter,
    QSplitterHandle, QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)

from dialogs import PitchDialog, SplitDialog
from loop_bar import LoopBar, _ms_to_str
from project import load_prefs, load_project, save_prefs, save_project, update_recent
from track import TrackData, TrackRow, track_color

_STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"]
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


class _CollapseHandle(QSplitterHandle):
    clicked = pyqtSignal()

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._collapsed = False

    def sizeHint(self):
        return QSize(5, 0)

    def minimumSizeHint(self):
        return QSize(5, 0)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        self.setToolTip("Show controls" if collapsed else "Hide controls")
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(QPen(QColor(140, 140, 140), 1))
        cx = self.width() / 2
        for cy in (self.height() / 2 - 7, self.height() / 2 + 7):
            if self._collapsed:
                p.drawPolyline(QPointF(cx + 1.5, cy - 3), QPointF(cx - 1.5, cy), QPointF(cx + 1.5, cy + 3))
            else:
                p.drawPolyline(QPointF(cx - 1.5, cy - 3), QPointF(cx + 1.5, cy), QPointF(cx - 1.5, cy + 3))


class _CollapsibleSplitter(QSplitter):
    toggle_requested = pyqtSignal()

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._handle = None

    def createHandle(self):
        self._handle = _CollapseHandle(self.orientation(), self)
        self._handle.clicked.connect(self.toggle_requested)
        return self._handle

    def set_collapsed_visual(self, collapsed: bool):
        if self._handle is not None:
            self._handle.set_collapsed(collapsed)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("mutr")
        self.resize(900, 520)
        self.setAcceptDrops(True)

        self._tracks: list[TrackData] = []
        self._players: list[tuple[QMediaPlayer, QAudioOutput]] = []
        self._track_rows: list[TrackRow] = []
        self._current_project: Path | None = None
        self._prefs = load_prefs()
        self._pending_seek_ms: float = 0.0
        self._expanded_video_track: int = -1
        self._solo_track: int = -1
        self._pre_solo_mutes: list[bool] = []
        self._dirty: bool = False
        self._controls_collapsed: bool = False
        self._panel_width: int = 240

        self._build_ui()
        self._connect_signals()

        QTimer.singleShot(100, self._restore_controls_state)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setSpacing(4)
        outer.setContentsMargins(10, 10, 10, 6)

        outer.addLayout(self._build_topbar())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome())
        self._stack.addWidget(self._build_tracks_page())
        self._stack.setCurrentIndex(0)
        outer.addWidget(self._stack, stretch=1)

        self.setStatusBar(QStatusBar())

    def _sync_heights(self):
        for row in self._track_rows:
            row.panel_cell.setFixedHeight(row.left_widget.height())

    def _restore_controls_state(self):
        if not self._prefs.get("controls_collapsed", False):
            return
        self._controls_collapsed = True
        sizes = self._tracks_split.sizes()
        if sizes[1] > 0:
            self._panel_width = sizes[1]
            self._tracks_split.setSizes([sum(sizes), 0])
        self._tracks_split.set_collapsed_visual(True)

    def _on_controls_toggle_requested(self):
        sizes = self._tracks_split.sizes()
        total = sum(sizes)
        if sizes[1] > 0:
            self._panel_width = sizes[1]
            self._tracks_split.setSizes([total, 0])
            self._controls_collapsed = True
        else:
            w = self._panel_width if self._panel_width > 0 else 240
            self._tracks_split.setSizes([max(50, total - w), w])
            self._controls_collapsed = False
        self._tracks_split.set_collapsed_visual(self._controls_collapsed)
        self._prefs["controls_collapsed"] = self._controls_collapsed
        save_prefs(self._prefs)

    def _build_tracks_page(self) -> QWidget:
        self._tracks_split = _CollapsibleSplitter(Qt.Orientation.Horizontal)
        self._tracks_split.toggle_requested.connect(self._on_controls_toggle_requested)
        self._tracks_split.setHandleWidth(5)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setSpacing(4)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._tracks_scroll = QScrollArea()
        self._tracks_scroll.setWidgetResizable(True)
        self._tracks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tracks_container = QWidget()
        self._tracks_layout = QVBoxLayout(self._tracks_container)
        self._tracks_layout.setSpacing(2)
        self._tracks_layout.setContentsMargins(0, 0, 0, 0)
        self._tracks_layout.addStretch()
        self._tracks_scroll.setWidget(self._tracks_container)
        left_layout.addWidget(self._tracks_scroll, stretch=1)

        left_layout.addWidget(self._build_transport())

        self._loop_bar = LoopBar()
        self._loop_bar.setEnabled(False)
        loop_container = QWidget()
        lc = QHBoxLayout(loop_container)
        lc.setContentsMargins(4, 0, 0, 0)
        lc.setSpacing(6)
        swatch = QLabel("")
        swatch.setFixedWidth(8)
        swatch.setStyleSheet("background: #666666;")
        lc.addWidget(swatch)
        name_lbl = QLabel("Looper")
        name_lbl.setFixedWidth(90)
        lc.addWidget(name_lbl)
        lc.addWidget(self._loop_bar, stretch=1)
        self._tracks_layout.insertWidget(0, loop_container)

        right_col = QWidget()
        right_col.setMinimumWidth(0)
        right_col.setStyleSheet("background: #dcdcdc;")
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._panels_scroll = QScrollArea()
        self._panels_scroll.setWidgetResizable(True)
        self._panels_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._panels_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._panels_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panels_container = QWidget()
        panels_container.setStyleSheet("background: #dcdcdc;")
        self._panels_layout = QVBoxLayout(panels_container)
        self._panels_layout.setSpacing(2)
        self._panels_layout.setContentsMargins(0, 0, 0, 0)

        self._loop_panel = QFrame()
        self._loop_panel.setObjectName("controlsPanel")
        self._loop_panel.setStyleSheet(
            "QFrame#controlsPanel { background: #dcdcdc;"
            " border: 1px solid #c8c8c8; border-radius: 4px; }")
        self._loop_panel.setFixedHeight(LoopBar._BAR_H + LoopBar._LABEL_H)
        lp = QHBoxLayout(self._loop_panel)
        lp.setContentsMargins(5, 2, 5, 2)
        lp.setSpacing(6)
        self._loop_btn = QPushButton("🔁")
        self._loop_btn.setCheckable(True)
        self._loop_btn.setFixedSize(24, 24)
        self._loop_btn.setToolTip("Loop")
        self._loop_btn.setEnabled(False)
        lp.addWidget(self._loop_btn)
        lp.addStretch()
        self._panels_layout.addWidget(self._loop_panel)

        self._panels_layout.addStretch()
        self._panels_scroll.setWidget(panels_container)
        right_layout.addWidget(self._panels_scroll)

        self._tracks_scroll.verticalScrollBar().valueChanged.connect(
            self._panels_scroll.verticalScrollBar().setValue)

        self._tracks_split.addWidget(left_col)
        self._tracks_split.addWidget(right_col)
        self._tracks_split.setStretchFactor(0, 1)
        self._tracks_split.setStretchFactor(1, 0)
        self._tracks_split.setCollapsible(0, False)
        self._tracks_split.setCollapsible(1, True)
        self._tracks_split.setSizes([700, 240])

        return self._tracks_split

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("mutr")
        title_font = QFont()
        title_font.setPointSize(28)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(20)

        self._welcome_grid = QGridLayout()
        self._welcome_grid.setSpacing(12)
        layout.addLayout(self._welcome_grid)
        layout.addStretch()

        self._refresh_welcome()
        return w

    def _refresh_welcome(self):
        while self._welcome_grid.count():
            child = self._welcome_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        recents = self._prefs.get("recent_projects", [])
        cols = 3
        for i, path in enumerate(recents):
            p = Path(path)
            btn = QPushButton(p.parent.name if p.suffix == ".mutrproj" else p.name)
            btn.setMinimumSize(QSize(180, 80))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, p2=path: self._open_recent(p2))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, p2=path: self._welcome_context_menu(pos, p2))
            self._welcome_grid.addWidget(btn, i // cols, i % cols)

        idx = len(recents)

        open_btn = QPushButton("Open…")
        open_btn.setMinimumSize(QSize(180, 80))
        open_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        open_btn.clicked.connect(self._open_from_welcome)
        self._welcome_grid.addWidget(open_btn, idx // cols, idx % cols)
        idx += 1

        new_btn = QPushButton("+ New Project")
        new_btn.setMinimumSize(QSize(180, 80))
        new_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        new_btn.setStyleSheet("QPushButton { border: 2px dashed #888; }")
        new_btn.clicked.connect(self._new_project)
        self._welcome_grid.addWidget(new_btn, idx // cols, idx % cols)

    def _welcome_context_menu(self, pos, path: str):
        menu = QMenu()
        remove_act = menu.addAction("Remove from Recents")
        act = menu.exec(self.sender().mapToGlobal(pos))
        if act == remove_act:
            recents = self._prefs.get("recent_projects", [])
            if path in recents:
                recents.remove(path)
                save_prefs(self._prefs)
                self._refresh_welcome()
                self._refresh_recent_menu()

    def _open_from_welcome(self):
        start_dir = self._prefs.get("last_project_dir", "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Open", start_dir,
            "All supported (*.mutrproj *.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.flac *.m4a *.ogg);;"
            "Projects (*.mutrproj);;Audio/Video (*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.flac *.m4a *.ogg)",
        )
        if not path:
            return
        p = Path(path)
        if p.suffix == ".mutrproj":
            self._open_project(path)
        else:
            self._add_track_file(path)

    def _open_recent(self, path: str):
        p = Path(path)
        if p.suffix == ".mutrproj":
            self._open_project(path)
        else:
            self._add_track_file(path)

    def _show_tracks_page(self):
        self._stack.setCurrentIndex(1)
        self._loop_bar.setEnabled(True)
        self._play_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._loop_btn.setEnabled(True)

    def _build_topbar(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._file_menu = QMenu(self)
        self._file_menu.addAction("Open Project…", self._open_project)
        self._file_menu.addSeparator()
        self._file_menu.addAction("Save Project", self._save_project)
        self._file_menu.addAction("Save As…", self._save_project_as)
        self._file_menu.addSeparator()
        self._file_menu.addAction("Add Track…", self._add_track_file)
        self._file_menu.addSeparator()
        self._file_menu.addAction("Close", self._close_project)
        self._recent_menu = QMenu("Recent", self)
        self._file_menu.addMenu(self._recent_menu)
        self._file_menu.aboutToShow.connect(self._refresh_recent_menu)

        self._file_btn = QPushButton("File")
        self._file_btn.setMenu(self._file_menu)
        row.addWidget(self._file_btn)

        self._help_btn = QPushButton("?")
        self._help_btn.setFixedWidth(28)

        row.addStretch()
        row.addWidget(self._help_btn)
        return row

    def _build_transport(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("transportBar")
        bar.setStyleSheet(
            "QFrame#transportBar { background: #dcdcdc; border-radius: 6px; }"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)

        speed_lbl = QLabel("Speed")
        speed_font = speed_lbl.font()
        speed_font.setPointSize(14)
        speed_lbl.setFont(speed_font)
        row.addWidget(speed_lbl)
        self._speed_val = 100
        self._speed_btn_down = QPushButton("−")
        self._speed_btn_down.setFixedSize(36, 40)
        self._speed_btn_up = QPushButton("+")
        self._speed_btn_up.setFixedSize(36, 40)
        self._speed_btn_max = QPushButton("⏩")
        self._speed_btn_max.setFixedSize(36, 40)
        self._speed_btn_max.setToolTip("Reset to 100%")
        for b in (self._speed_btn_down, self._speed_btn_up, self._speed_btn_max):
            f = b.font()
            f.setPointSize(15)
            b.setFont(f)
        self._speed_lbl = QLabel("1.00×")
        self._speed_lbl.setFixedWidth(60)
        self._speed_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        speed_val_font = self._speed_lbl.font()
        speed_val_font.setPointSize(15)
        self._speed_lbl.setFont(speed_val_font)
        row.addWidget(self._speed_btn_down)
        row.addWidget(self._speed_lbl)
        row.addWidget(self._speed_btn_up)
        row.addWidget(self._speed_btn_max)

        row.addStretch()

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(64, 44)
        self._play_btn.setEnabled(False)
        play_font = self._play_btn.font()
        play_font.setPointSize(18)
        self._play_btn.setFont(play_font)
        row.addWidget(self._play_btn)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedSize(48, 44)
        self._stop_btn.setEnabled(False)
        stop_font = self._stop_btn.font()
        stop_font.setPointSize(14)
        self._stop_btn.setFont(stop_font)
        row.addWidget(self._stop_btn)

        self._time_lbl = QLabel("0:00 / 0:00")
        time_font = self._time_lbl.font()
        time_font.setPointSize(15)
        self._time_lbl.setFont(time_font)
        row.addWidget(self._time_lbl)

        row.addStretch()

        vol_lbl = QLabel("Vol")
        vol_font = vol_lbl.font()
        vol_font.setPointSize(14)
        vol_lbl.setFont(vol_font)
        row.addWidget(vol_lbl)
        self._master_vol = QSlider(Qt.Orientation.Horizontal)
        self._master_vol.setRange(0, 100)
        self._master_vol.setValue(80)
        self._master_vol.setFixedSize(150, 40)
        self._master_vol_lbl = QLabel("80%")
        self._master_vol_lbl.setFixedWidth(54)
        vol_val_font = self._master_vol_lbl.font()
        vol_val_font.setPointSize(15)
        self._master_vol_lbl.setFont(vol_val_font)
        row.addWidget(self._master_vol)
        row.addWidget(self._master_vol_lbl)

        return bar

    # ── signal wiring ─────────────────────────────────────────────────────────

    def _connect_signals(self):
        self._help_btn.clicked.connect(self._show_help)
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)
        self._loop_btn.toggled.connect(self._on_loop_toggled)
        self._speed_btn_down.clicked.connect(lambda: self._on_speed_step(-10))
        self._speed_btn_up.clicked.connect(lambda: self._on_speed_step(10))
        self._speed_btn_max.clicked.connect(lambda: self._on_speed_step(100))
        self._master_vol.valueChanged.connect(self._on_master_volume)
        self._loop_bar.seek_requested.connect(self._sync_seek)
        self._loop_bar.segment_selected.connect(self._on_segment_selected)
        self._loop_bar.markers_changed.connect(self._on_markers_changed)
        self._media_devices = QMediaDevices()
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)

        shortcuts = [
            ("Space", self._toggle_play),
            ("L", self._loop_btn.toggle),
            ("Left", lambda: self._seek_by_seconds(-1)),
            ("Right", lambda: self._seek_by_seconds(1)),
            ("Up", self._seek_prev_segment),
            ("Down", lambda: self._seek_to_segment(1)),
            ("D", self._delete_nearest_marker),
            ("V", self._toggle_video),
            ("C", self._on_controls_toggle_requested),
        ]
        for key, slot in shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(slot)

    # ── track management ─────────────────────────────────────────────────────

    def _add_track(self, data: TrackData, auto_play: bool = False):
        idx = len(self._tracks)
        self._tracks.append(data)

        row = TrackRow(idx, data, default_video_height=self._prefs.get("video_height", 480))
        row.mute_toggled.connect(self._on_mute)
        row.volume_changed.connect(self._on_volume_changed)
        row.pitch_shift_requested.connect(self._on_pitch_shift_requested)
        row.split_requested.connect(self._on_split_requested)
        row.remove_requested.connect(self._on_remove_track_requested)
        row.name_changed.connect(self._on_track_renamed)
        row.show_in_finder_requested.connect(self._on_show_in_finder)
        row.show_video_requested.connect(self._on_show_video_for_track)
        row.solo_requested.connect(self._on_solo)
        row.video_resized.connect(self._on_video_resized)
        row.seek_requested.connect(self._on_track_seek)
        self._track_rows.append(row)
        self._tracks_layout.insertWidget(idx, row.left_widget)
        self._panels_layout.insertWidget(idx, row.panel_cell)
        self._sync_heights()

        player = QMediaPlayer()
        audio_out = QAudioOutput()
        player.setAudioOutput(audio_out)

        if idx == 0:
            print(f"[video] player created for idx=0, file={data.file}")
            player.positionChanged.connect(self._on_position)
            player.durationChanged.connect(self._on_duration)
            player.playbackStateChanged.connect(self._on_play_state)
            player.mediaStatusChanged.connect(self._on_media_status)

        self._players.append((player, audio_out))
        self._apply_volume(idx)
        is_vid = Path(data.file).suffix.lower() in _VIDEO_EXTS
        print(f"[video] _add_track idx={idx}, file={data.file}, is_video={is_vid}")
        if is_vid:
            print(f"[video] setting video output for player idx={idx} before setSource")
            player.setVideoOutput(row.video_widget)
        player.setSource(QUrl.fromLocalFile(data.file))

        if idx > 0:
            player.setPosition(int(self._current_pos()))
            if self._is_playing():
                player.play()

        if auto_play:
            player.play()

        self._dirty = True

        if idx == 0:
            self._show_tracks_page()
            self._loop_bar.setEnabled(True)
            self._loop_btn.setEnabled(True)

    def _clear_tracks(self):
        if self._players:
            p0 = self._players[0][0]
            p0.positionChanged.disconnect(self._on_position)
            p0.durationChanged.disconnect(self._on_duration)
            p0.playbackStateChanged.disconnect(self._on_play_state)
            p0.mediaStatusChanged.disconnect(self._on_media_status)
        for p, _ in self._players:
            p.stop()
            p.setSource(QUrl())
        self._players.clear()
        self._tracks.clear()
        for row in self._track_rows:
            row.cleanup()
            self._tracks_layout.removeWidget(row.left_widget)
            self._panels_layout.removeWidget(row.panel_cell)
            row.left_widget.deleteLater()
            row.panel_cell.deleteLater()
            row.deleteLater()
        self._track_rows.clear()
        self._expanded_video_track = -1
        self._loop_bar.set_total(0.0)
        self._loop_bar.set_markers([])
        self._loop_bar.set_active_segment(-1)
        self._loop_bar.setEnabled(False)
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._loop_btn.setEnabled(False)
        self._loop_btn.setChecked(False)
        self._time_lbl.setText("0:00 / 0:00")
        self._solo_track = -1
        self._pre_solo_mutes = []
        self._dirty = False
        self._stack.setCurrentIndex(0)

    # ── playback sync ─────────────────────────────────────────────────────────

    def _sync_play(self):
        for p, _ in self._players:
            p.play()

    def _sync_pause(self):
        for p, _ in self._players:
            p.pause()

    def _sync_seek(self, ms: float):
        for p, _ in self._players:
            p.setPosition(int(ms))

    def _sync_rate(self, rate: float):
        for p, _ in self._players:
            p.setPlaybackRate(rate)

    def _on_audio_outputs_changed(self):
        dev = QMediaDevices.defaultAudioOutput()
        for _, audio_out in self._players:
            audio_out.setDevice(dev)

    def _apply_volume(self, idx: int):
        if idx >= len(self._players):
            return
        _, audio_out = self._players[idx]
        data = self._tracks[idx]
        master = self._master_vol.value() / 100.0
        vol = 0.0 if data.muted else data.volume * master
        audio_out.setVolume(vol)

    def _apply_all_volumes(self):
        for i in range(len(self._tracks)):
            self._apply_volume(i)

    def _current_pos(self) -> float:
        if not self._players:
            return 0.0
        return float(self._players[0][0].position())

    def _is_playing(self) -> bool:
        if not self._players:
            return False
        return self._players[0][0].playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _toggle_play(self):
        if self._is_playing():
            self._sync_pause()
        else:
            self._sync_play()

    def _stop(self):
        self._sync_pause()
        self._sync_seek(0.0)

    # ── player event handlers ─────────────────────────────────────────────────

    def _on_position(self, pos_ms: int):
        ms = float(pos_ms)

        if self._loop_btn.isChecked():
            bounds = self._loop_bar.get_segment_bounds(self._loop_bar._active_segment)
            if bounds and ms >= bounds[1]:
                self._sync_seek(bounds[0])
                return

        self._loop_bar.set_playhead(ms)
        dur = float(self._players[0][0].duration())
        self._time_lbl.setText(f"{_ms_to_str(ms)} / {_ms_to_str(dur)}")

        ratio = (ms / dur) if dur > 0 else 0.0
        for row in self._track_rows:
            row.set_playhead_ratio(ratio)

    def _on_duration(self, dur_ms: int):
        self._loop_bar.set_total(float(dur_ms))

    def _on_play_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("⏸" if playing else "▶")

    def _on_media_status(self, status):
        print(f"[video] mediaStatus → {status}")
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            p0 = self._players[0][0]
            print(f"[video] LoadedMedia: hasVideo={p0.hasVideo()}, duration={p0.duration()}, error={p0.error()}, errorString={p0.errorString()}")
            if self._pending_seek_ms > 0:
                seek_ms = self._pending_seek_ms
                self._pending_seek_ms = 0.0
                QTimer.singleShot(100, lambda ms=seek_ms: self._sync_seek(ms))
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop_btn.isChecked():
                bounds = self._loop_bar.get_segment_bounds(self._loop_bar._active_segment)
                start = bounds[0] if bounds else 0.0
                self._sync_seek(start)
                self._sync_play()
            else:
                self._stop()

    # ── video ─────────────────────────────────────────────────────────────────

    def _on_track_seek(self, _track_idx: int, ratio: float):
        if not self._players:
            return
        dur = float(self._players[0][0].duration())
        if dur <= 0:
            return
        self._sync_seek(ratio * dur)

    def _on_video_resized(self, height: int):
        self._prefs["video_height"] = height
        save_prefs(self._prefs)
        self._sync_heights()

    def _toggle_video(self):
        for i, row in enumerate(self._track_rows):
            if row._video_btn is not None:
                self._on_show_video_for_track(i)
                return

    def _on_show_video_for_track(self, track_idx: int):
        if self._expanded_video_track == track_idx:
            self._track_rows[track_idx].set_video_visible(False)
            self._expanded_video_track = -1
            self._sync_heights()
            self._persist_ui_state()
            return

        if self._expanded_video_track >= 0 and self._expanded_video_track < len(self._track_rows):
            self._track_rows[self._expanded_video_track].set_video_visible(False)

        p = self._players[track_idx][0]
        row = self._track_rows[track_idx]
        p.setVideoOutput(row.video_widget)
        row.set_video_visible(True)
        self._expanded_video_track = track_idx
        self._sync_heights()
        self._persist_ui_state()

    def _persist_ui_state(self):
        if self._current_project is not None:
            self._write_project(self._current_project)

    # ── loop / segment ────────────────────────────────────────────────────────

    def _on_loop_toggled(self, on: bool):
        self._loop_bar.set_loop_active(on)
        self._loop_btn.setStyleSheet("background: #7a6a00; color: #ffe066;" if on else "")
        self._dirty = True

    def _on_markers_changed(self, markers: list):
        self._dirty = True

    def _on_segment_selected(self, seg_idx: int):
        self._loop_bar.set_active_segment(seg_idx)
        self._dirty = True

    def _seek_by_seconds(self, delta: int):
        if not self._players:
            return
        dur = float(self._players[0][0].duration())
        ms = max(0.0, min(dur, self._current_pos() + delta * 1000.0))
        self._sync_seek(ms)

    def _seek_to_segment(self, delta: int):
        n_segs = len(self._loop_bar._markers) + 1
        if n_segs < 2:
            return
        cur = max(0, self._loop_bar._active_segment)
        idx = max(0, min(n_segs - 1, cur + delta))
        if idx == cur:
            return
        self._loop_bar.set_active_segment(idx)
        bounds = self._loop_bar.get_segment_bounds(idx)
        if bounds:
            self._sync_seek(bounds[0])

    def _seek_prev_segment(self):
        markers = self._loop_bar._markers
        n_segs = len(markers) + 1
        if n_segs < 2 or not self._players:
            return
        total = float(self._players[0][0].duration())
        pos = self._current_pos()
        all_m = [0.0] + markers + [total]
        cur = max(0, len(all_m) - 2)
        for i in range(len(all_m) - 1):
            if all_m[i] <= pos < all_m[i + 1]:
                cur = i
                break
        if pos - all_m[cur] > 1000.0:
            target = cur
        else:
            target = max(0, cur - 1)
        self._loop_bar.set_active_segment(target)
        self._sync_seek(all_m[target])

    def _delete_nearest_marker(self):
        markers = list(self._loop_bar._markers)
        if not markers:
            return
        now = self._current_pos()
        nearest = min(markers, key=lambda m: abs(m - now))
        markers.remove(nearest)
        n_segs = len(markers) + 1
        if self._loop_bar._active_segment >= n_segs:
            self._loop_bar.set_active_segment(n_segs - 1)
        self._loop_bar.set_markers(markers)
        self._dirty = True

    # ── track event handlers ──────────────────────────────────────────────────

    def _on_mute(self, track_idx: int, muted: bool):
        self._tracks[track_idx].muted = muted
        self._apply_volume(track_idx)
        self._dirty = True

    def _on_volume_changed(self, track_idx: int, vol: float):
        self._tracks[track_idx].volume = vol
        self._apply_volume(track_idx)
        self._dirty = True

    def _on_speed_step(self, delta: int):
        self._speed_val = max(10, min(100, self._speed_val + delta))
        rate = self._speed_val / 100.0
        self._speed_lbl.setText(f"{rate:.2f}×")
        self._sync_rate(rate)
        self._dirty = True

    def _on_master_volume(self, value: int):
        self._master_vol_lbl.setText(f"{value}%")
        self._apply_all_volumes()
        self._dirty = True

    # ── solo ──────────────────────────────────────────────────────────────────

    def _on_solo(self, track_idx: int):
        if self._solo_track == track_idx:
            self._solo_track = -1
            for i, data in enumerate(self._tracks):
                data.muted = self._pre_solo_mutes[i] if i < len(self._pre_solo_mutes) else False
                self._track_rows[i].set_muted(data.muted)
                self._apply_volume(i)
            self._pre_solo_mutes = []
        else:
            if self._solo_track == -1:
                self._pre_solo_mutes = [d.muted for d in self._tracks]
            self._solo_track = track_idx
            for i, data in enumerate(self._tracks):
                data.muted = (i != track_idx)
                self._track_rows[i].set_muted(data.muted)
                self._apply_volume(i)
        for i, row in enumerate(self._track_rows):
            row.set_solo_active(i == self._solo_track)

    def _on_remove_track_requested(self, track_idx: int):
        if len(self._tracks) <= 1:
            return
        name = self._tracks[track_idx].name
        file_path = self._tracks[track_idx].file
        msg = QMessageBox(self)
        msg.setWindowTitle("Remove Track")
        msg.setText(f"Remove \"{name}\"?")
        msg.setInformativeText("Remove only the track, or also delete the file from disk?")
        remove_btn = msg.addButton("Remove Only", QMessageBox.ButtonRole.DestructiveRole)
        delete_btn = msg.addButton("Remove && Delete File", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == cancel_btn:
            return
        delete_file = (clicked == delete_btn)
        p, _ = self._players[track_idx]
        p.stop()
        p.setSource(QUrl())
        del self._players[track_idx]
        del self._tracks[track_idx]
        row = self._track_rows.pop(track_idx)
        row.cleanup()
        self._tracks_layout.removeWidget(row.left_widget)
        self._panels_layout.removeWidget(row.panel_cell)
        row.left_widget.deleteLater()
        row.panel_cell.deleteLater()
        row.deleteLater()
        self._sync_heights()
        for i, r in enumerate(self._track_rows):
            r._idx = i
        if self._solo_track == track_idx:
            for i, r in enumerate(self._track_rows):
                r.set_solo_active(False)
            self._solo_track = -1
            self._pre_solo_mutes = []
        elif self._solo_track > track_idx:
            self._solo_track -= 1
        if self._expanded_video_track == track_idx:
            self._expanded_video_track = -1
        elif self._expanded_video_track > track_idx:
            self._expanded_video_track -= 1
        if delete_file:
            try:
                Path(file_path).unlink(missing_ok=True)
            except Exception:
                pass
        self._dirty = True

    def _on_track_renamed(self, track_idx: int, new_name: str):
        data = self._tracks[track_idx]
        old_name = data.name
        data.name = new_name
        old_path = Path(data.file)
        new_path = old_path.parent / (new_name + old_path.suffix)
        if old_path.exists() and new_path != old_path:
            try:
                old_path.rename(new_path)
                data.file = str(new_path)
                if data.source_file == str(old_path):
                    data.source_file = str(new_path)
                player = self._players[track_idx][0]
                player.setSource(QUrl.fromLocalFile(str(new_path)))
            except Exception:
                pass
        self._dirty = True

    def _on_show_in_finder(self, track_idx: int):
        file_path = str(Path(self._tracks[track_idx].file).resolve())
        subprocess.run(["open", "-R", file_path])

    # ── pitch shift (via dialog) ──────────────────────────────────────────────

    def _on_pitch_shift_requested(self, track_idx: int):
        data = self._tracks[track_idx]
        dlg = PitchDialog(data.source_file, parent=self)
        dlg.applied.connect(lambda path, st, i=track_idx: self._add_pitch_shifted_track(i, path, st))
        dlg.exec()

    def _add_pitch_shifted_track(self, track_idx: int, new_path: str, semitones: int):
        if semitones == 0:
            return
        original = self._tracks[track_idx]
        sign = "+" if semitones > 0 else ""
        name = f"{original.name} {sign}{semitones} st"
        idx = len(self._tracks)
        data = TrackData(
            name=name,
            file=new_path,
            source_file=new_path,
            color=track_color(idx),
            pitch_baked=semitones,
        )
        self._add_track(data)
        self._dirty = True

    # ── stem split (via dialog) ───────────────────────────────────────────────

    def _on_split_requested(self, track_idx: int):
        data = self._tracks[track_idx]
        proj_dir = self._project_dir()
        base_dir = proj_dir if proj_dir is not None else Path(data.source_file).parent
        out_dir = str(base_dir)
        dlg = SplitDialog(data.source_file, out_dir, parent=self)
        dlg.finished_stems.connect(self._on_stems_done)
        dlg.exec()

    def _on_stems_done(self, stems: dict):
        self.statusBar().showMessage(f"Stems ready: {', '.join(stems.keys())}", 5000)
        ordered = sorted(stems.keys(), key=lambda k: _STEM_ORDER.index(k) if k in _STEM_ORDER else len(_STEM_ORDER))
        for name in ordered:
            path = stems[name]
            idx = len(self._tracks)
            data = TrackData(
                name=name.capitalize(),
                file=path,
                source_file=path,
                color=track_color(idx),
            )
            self._add_track(data)
        self._dirty = True

    # ── project ───────────────────────────────────────────────────────────────

    def _project_dir(self) -> Path | None:
        if self._current_project is None:
            return None
        return self._current_project.parent

    def _copy_to_project(self, src: str) -> str:
        proj_dir = self._project_dir()
        if proj_dir is None:
            return src
        src_path = Path(src)
        if not src_path.exists():
            return src
        dst = proj_dir / src_path.name
        if dst.exists() and dst.samefile(src_path):
            return str(dst)
        return str(shutil.copy2(src_path, dst))

    def _close_project(self):
        if not self._maybe_save():
            return
        self._clear_tracks()
        self._current_project = None
        self.setWindowTitle("mutr")

    def _new_project(self):
        if self._tracks:
            reply = QMessageBox.question(
                self, "New Project", "Close the current project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._clear_tracks()
        projects_dir = Path.home() / ".mutr" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        name = "New Project"
        proj_dir = projects_dir / name
        counter = 1
        while proj_dir.exists():
            proj_dir = projects_dir / f"New Project {counter}"
            counter += 1
        proj_dir.mkdir(parents=True)
        self._current_project = proj_dir / f"{name}.mutrproj"
        self.setWindowTitle(f"mutr — {proj_dir.name}")
        self._show_tracks_page()

    def _add_track_file(self, path: str = ""):
        if not path:
            start_dir = self._prefs.get("last_audio_dir", "")
            path, _ = QFileDialog.getOpenFileName(
                self, "Add Audio / Video Track", start_dir,
                "Audio/Video (*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.flac *.m4a *.ogg)",
            )
        if not path:
            return
        self._prefs["last_audio_dir"] = str(Path(path).parent)
        path = self._copy_to_project(path)
        idx = len(self._tracks)
        name = Path(path).stem
        data = TrackData(name=name, file=path, source_file=path, color=track_color(idx))
        self._add_track(data)

    def _save_project(self):
        if self._current_project is None:
            self._save_project_as()
        else:
            self._write_project(self._current_project)

    def _default_project_name(self) -> str:
        if self._tracks:
            return self._tracks[0].name
        return "My Project"

    def _save_project_as(self):
        proj_dir = self._project_dir()
        default_name = self._default_project_name()
        if proj_dir is None:
            name, ok = QInputDialog.getText(self, "Save Project As", "Project name:", text=default_name)
            if not ok or not (name := name.strip()):
                return
            projects_dir = Path.home() / ".mutr" / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            proj_dir = projects_dir / name
            if proj_dir.exists():
                QMessageBox.critical(self, "Error", f"Project \"{name}\" already exists.")
                return
            proj_dir.mkdir(parents=True)
            proj_file = proj_dir / f"{name}.mutrproj"
            self._current_project = proj_file
            self._write_project(proj_file)
            self._update_recent(str(proj_file))
            self._refresh_welcome()
            self._show_tracks_page()
            self.setWindowTitle(f"mutr — {name}")
            return
        name, ok = QInputDialog.getText(self, "Save Project As", "Project name:", text=default_name)
        if not ok or not (name := name.strip()):
            return
        new_dir = proj_dir.parent / name
        if new_dir.exists() and new_dir != proj_dir:
            QMessageBox.critical(self, "Error", f"Project \"{name}\" already exists.")
            return
        old_proj_file = self._current_project
        proj_dir.rename(new_dir)
        moved_old = new_dir / old_proj_file.name
        new_proj_file = new_dir / f"{name}.mutrproj"
        if moved_old.exists() and moved_old != new_proj_file:
            moved_old.rename(new_proj_file)
        old_dir_s = str(proj_dir)
        new_dir_s = str(new_dir)
        for data in self._tracks:
            if data.file.startswith(old_dir_s):
                data.file = data.file.replace(old_dir_s, new_dir_s)
            if data.source_file.startswith(old_dir_s):
                data.source_file = data.source_file.replace(old_dir_s, new_dir_s)
        for p, _ in self._players:
            src = p.source()
            if src.isValid() and src.toLocalFile().startswith(old_dir_s):
                p.setSource(QUrl.fromLocalFile(src.toLocalFile().replace(old_dir_s, new_dir_s)))
        self._current_project = new_proj_file
        self._write_project(new_proj_file)
        self._update_recent(str(new_proj_file))
        recents = self._prefs.get("recent_projects", [])
        cleaned = []
        for r in recents:
            if r.startswith(old_dir_s):
                relocated = r.replace(old_dir_s, new_dir_s)
                if Path(relocated).exists():
                    cleaned.append(relocated)
            else:
                cleaned.append(r)
        self._prefs["recent_projects"] = cleaned
        save_prefs(self._prefs)
        self._refresh_welcome()
        self._refresh_recent_menu()
        self.setWindowTitle(f"mutr — {name}")

    def _write_project(self, path: Path):
        state = {
            "version": 1,
            "tracks": [t.to_dict() for t in self._tracks],
            "markers": list(self._loop_bar._markers),
            "active_segment": self._loop_bar._active_segment,
            "loop_enabled": self._loop_btn.isChecked(),
            "speed": self._speed_val,
            "master_volume": self._master_vol.value(),
            "position_ms": self._current_pos(),
            "expanded_video_track": self._expanded_video_track,
        }
        try:
            save_project(path, state)
            self._dirty = False
            self.statusBar().showMessage(f"Saved to {path.name}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))

    def _open_project(self, path: str = ""):
        if not path:
            start_dir = self._prefs.get("last_project_dir", "")
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Project", start_dir, "mutr project (*.mutrproj)"
            )
        if not path:
            return
        try:
            state = load_project(Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Load error", str(e))
            return

        self._clear_tracks()
        self._current_project = Path(path)

        pos = state.get("position_ms", 0.0)
        if pos > 0:
            self._pending_seek_ms = pos

        for i, t in enumerate(state["tracks"]):
            if not Path(t["file"]).exists():
                QMessageBox.warning(self, "Missing file",
                    f"Track file not found:\n{t['file']}\nSkipping.")
                continue
            data = TrackData.from_dict(t, track_color(i))
            self._add_track(data)

        if state.get("markers"):
            self._loop_bar.set_markers(state["markers"])
        if state.get("active_segment", -1) >= 0:
            self._loop_bar.set_active_segment(state["active_segment"])

        saved_speed = state.get("speed", 100)
        self._speed_val = saved_speed
        self._speed_lbl.setText(f"{saved_speed / 100.0:.2f}×")
        self._sync_rate(saved_speed / 100.0)
        self._master_vol.setValue(state.get("master_volume", 80))

        if state.get("loop_enabled", False):
            self._loop_btn.setChecked(True)
            self._loop_bar.set_loop_active(True)

        expanded = state.get("expanded_video_track", -1)
        if expanded >= 0 and expanded < len(self._track_rows):
            self._on_show_video_for_track(expanded)

        self._prefs["last_project_dir"] = str(Path(path).parent)
        self._prefs["last_project"] = str(path)
        save_prefs(self._prefs)
        self._update_recent(path)
        self.setWindowTitle(f"mutr — {Path(path).parent.name}")
        self._dirty = False

    # ── prefs / recent ────────────────────────────────────────────────────────

    def _update_recent(self, path: str):
        update_recent(self._prefs, path)
        save_prefs(self._prefs)
        self._refresh_recent_menu()
        self._refresh_welcome()

    def _refresh_recent_menu(self):
        self._recent_menu.clear()
        recents = self._prefs.get("recent_projects", [])
        if not recents:
            act = self._recent_menu.addAction("(none)")
            act.setEnabled(False)
            return
        for path in recents:
            p = Path(path)
            act = self._recent_menu.addAction(p.name)
            act.setToolTip(path)
            if not p.exists():
                act.setEnabled(False)
            else:
                act.triggered.connect(lambda checked, p=path: self._open_recent(p))

    # ── help ──────────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        audio_exts = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".flac", ".m4a", ".ogg"}
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in audio_exts:
                path = self._copy_to_project(path)
                idx = len(self._tracks)
                name = Path(path).stem
                data = TrackData(name=name, file=path, source_file=path, color=track_color(idx))
                self._add_track(data)

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)
        rows = [
            ("Space", "Play / Pause"),
            ("L", "Toggle loop on selected segment"),
            ("← →", "Seek ±1 second"),
            ("↑", "Replay segment start / previous segment"),
            ("↓", "Next segment"),
            ("D", "Delete nearest marker"),
            ("V", "Toggle video (first video track)"),
            ("C", "Show/hide controls panel"),
            ("Click waveform", "Seek to position"),
            ("Click ⚙", "Track actions: Pitch Shift, Split Stems, …"),
            ("Click panel divider", "Show/hide controls panel"),
            ("Double-click loop bar", "Add marker (snaps to second)"),
            ("Double-click marker", "Remove that marker"),
            ("Drag marker", "Move marker"),
        ]
        for key, desc in rows:
            row_w = QWidget()
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 2, 0, 2)
            key_lbl = QLabel(key)
            key_lbl.setFixedWidth(160)
            key_lbl.setStyleSheet(
                "font-family: monospace; background: #2a2a2a; color: #ddd;"
                "border-radius: 3px; padding: 2px 6px;"
            )
            row_h.addWidget(key_lbl)
            row_h.addSpacing(8)
            row_h.addWidget(QLabel(desc))
            row_h.addStretch()
            layout.addWidget(row_w)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addSpacing(8)
        layout.addWidget(close_btn)
        dlg.exec()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _maybe_save(self) -> bool:
        if not self._dirty:
            return True
        msg = QMessageBox(self)
        msg.setWindowTitle("Unsaved Changes")
        msg.setText("Save changes before closing?")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Save)
        reply = msg.exec()
        if reply == QMessageBox.StandardButton.Save:
            self._save_project()
            return not self._dirty
        elif reply == QMessageBox.StandardButton.Discard:
            return True
        return False

    def closeEvent(self, event):
        if not self._maybe_save():
            event.ignore()
            return
        save_prefs(self._prefs)
        try:
            self._clear_tracks()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.showMaximized()

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        QTimer.singleShot(0, lambda: win._add_track_file(arg))
    else:
        last = win._prefs.get("last_project")
        if last and Path(last).exists():
            QTimer.singleShot(100, lambda: win._open_project(last))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
