#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "PyQt6>=6.6.0",
#   "yt-dlp>=2024.1.0",
# ]
# ///

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSlider, QStatusBar, QVBoxLayout, QWidget,
)

from dialogs import DownloadDialog, PitchDialog, SplitDialog
from loop_bar import LoopBar, SeekSlider, _ms_to_str
from project import load_prefs, load_project, save_prefs, save_project, update_recent
from track import TrackData, TrackRow, track_color

_STEM_ORDER = ["vocals", "drums", "bass", "other"]


class VideoWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("mutr — Video")
        self.resize(640, 480)
        self._video_widget = QVideoWidget()
        self.setCentralWidget(self._video_widget)

    @property
    def video_widget(self) -> QVideoWidget:
        return self._video_widget

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("mutr")
        self.resize(900, 520)

        self._tracks: list[TrackData] = []
        self._players: list[tuple[QMediaPlayer, QAudioOutput]] = []
        self._track_rows: list[TrackRow] = []
        self._current_project: Path | None = None
        self._prefs = load_prefs()
        self._dragging = False
        self._pending_seek_ms: float = 0.0
        self._video_window = VideoWindow(self)
        self._solo_track: int = -1
        self._pre_solo_mutes: list[bool] = []

        self._build_ui()
        self._connect_signals()
        self._refresh_recent_menu()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setSpacing(4)
        outer.setContentsMargins(10, 10, 10, 6)

        outer.addLayout(self._build_topbar())

        self._tracks_scroll = QScrollArea()
        self._tracks_scroll.setWidgetResizable(True)
        self._tracks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tracks_container = QWidget()
        self._tracks_layout = QVBoxLayout(self._tracks_container)
        self._tracks_layout.setSpacing(2)
        self._tracks_layout.setContentsMargins(0, 0, 0, 0)
        self._tracks_layout.addStretch()
        self._tracks_scroll.setWidget(self._tracks_container)
        outer.addWidget(self._tracks_scroll, stretch=1)

        self._seek_slider = SeekSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setEnabled(False)
        outer.addWidget(self._seek_slider)

        self._loop_bar = LoopBar()
        self._loop_bar.setEnabled(False)
        outer.addWidget(self._loop_bar)

        outer.addLayout(self._build_transport())
        self.setStatusBar(QStatusBar())

    def _build_topbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._new_btn = QPushButton("New")
        self._open_proj_btn = QPushButton("Open Project…")
        self._save_proj_btn = QPushButton("Save Project")
        self._save_as_btn = QPushButton("Save As…")
        self._open_src_btn = QPushButton("Open File…")
        self._add_track_btn = QPushButton("+ Track")
        self._download_btn = QPushButton("⬇ Download…")
        self._recent_btn = QPushButton("Recent ▾")
        self._recent_btn.setFixedWidth(90)
        self._recent_menu = QMenu(self)
        self._recent_btn.setMenu(self._recent_menu)

        self._video_btn = QPushButton("⬛ Video")
        self._video_btn.setEnabled(False)
        self._video_btn.setToolTip("Open video window")
        self._video_btn.clicked.connect(self._show_video_window)

        self._help_btn = QPushButton("?")
        self._help_btn.setFixedWidth(28)

        for w in [self._new_btn, self._open_proj_btn, self._save_proj_btn,
                  self._save_as_btn, self._open_src_btn, self._add_track_btn,
                  self._download_btn, self._recent_btn]:
            row.addWidget(w)
        row.addStretch()
        row.addWidget(self._video_btn)
        row.addWidget(self._help_btn)
        return row

    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(40)
        self._play_btn.setEnabled(False)
        row.addWidget(self._play_btn)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedWidth(32)
        self._stop_btn.setEnabled(False)
        row.addWidget(self._stop_btn)

        self._time_lbl = QLabel("0:00 / 0:00")
        row.addWidget(self._time_lbl)
        row.addSpacing(12)

        self._loop_btn = QPushButton("Loop")
        self._loop_btn.setCheckable(True)
        self._loop_btn.setFixedWidth(52)
        self._loop_btn.setEnabled(False)
        row.addWidget(self._loop_btn)

        row.addStretch()

        row.addWidget(QLabel("Speed"))
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(25, 150)
        self._speed_slider.setValue(100)
        self._speed_slider.setFixedWidth(120)
        self._speed_lbl = QLabel("1.00×")
        self._speed_lbl.setFixedWidth(38)
        row.addWidget(self._speed_slider)
        row.addWidget(self._speed_lbl)
        row.addSpacing(12)

        row.addWidget(QLabel("Vol"))
        self._master_vol = QSlider(Qt.Orientation.Horizontal)
        self._master_vol.setRange(0, 100)
        self._master_vol.setValue(80)
        self._master_vol.setFixedWidth(100)
        self._master_vol_lbl = QLabel("80%")
        self._master_vol_lbl.setFixedWidth(36)
        row.addWidget(self._master_vol)
        row.addWidget(self._master_vol_lbl)

        return row

    # ── signal wiring ─────────────────────────────────────────────────────────

    def _connect_signals(self):
        self._new_btn.clicked.connect(self._new_project)
        self._open_proj_btn.clicked.connect(self._open_project)
        self._save_proj_btn.clicked.connect(self._save_project)
        self._save_as_btn.clicked.connect(self._save_project_as)
        self._open_src_btn.clicked.connect(self._open_source_file)
        self._add_track_btn.clicked.connect(self._add_track_clicked)
        self._download_btn.clicked.connect(self._open_downloader)
        self._help_btn.clicked.connect(self._show_help)
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)
        self._loop_btn.toggled.connect(self._on_loop_toggled)
        self._speed_slider.valueChanged.connect(self._on_speed)
        self._master_vol.valueChanged.connect(self._on_master_volume)
        self._seek_slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self._seek_slider.sliderReleased.connect(self._on_seek_released)
        self._loop_bar.seek_requested.connect(self._sync_seek)
        self._loop_bar.segment_selected.connect(self._on_segment_selected)

        shortcuts = [
            ("Space", self._toggle_play),
            ("L", self._loop_btn.toggle),
            ("Left", lambda: self._seek_by_seconds(-5)),
            ("Right", lambda: self._seek_by_seconds(5)),
            ("Up", lambda: self._seek_to_segment(-1)),
            ("Down", lambda: self._seek_to_segment(1)),
            ("D", self._delete_nearest_marker),
        ]
        for key, slot in shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(slot)

    # ── track management ─────────────────────────────────────────────────────

    def _add_track(self, data: TrackData, is_source: bool = False, auto_play: bool = False):
        idx = len(self._tracks)
        self._tracks.append(data)

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
        player.setSource(QUrl.fromLocalFile(data.file))

        if idx > 0:
            player.setPosition(int(self._current_pos()))
            if self._is_playing():
                player.play()

        if auto_play:
            player.play()

        row = TrackRow(idx, data, is_source=is_source)
        row.mute_toggled.connect(self._on_mute)
        row.volume_changed.connect(self._on_volume_changed)
        row.pitch_shift_requested.connect(self._on_pitch_shift_requested)
        row.split_requested.connect(self._on_split_requested)
        row.remove_requested.connect(self._on_remove_track_requested)
        row.name_changed.connect(self._on_track_renamed)
        row.show_in_finder_requested.connect(self._on_show_in_finder)
        row.solo_requested.connect(self._on_solo)
        self._track_rows.append(row)
        self._tracks_layout.insertWidget(self._tracks_layout.count() - 1, row)

        if idx == 0:
            self._play_btn.setEnabled(True)
            self._stop_btn.setEnabled(True)
            self._seek_slider.setEnabled(True)
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
            self._tracks_layout.removeWidget(row)
            row.deleteLater()
        self._track_rows.clear()
        self._video_btn.setEnabled(False)
        self._video_window.hide()
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setEnabled(False)
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
        if self._dragging:
            return
        ms = float(pos_ms)

        if self._loop_btn.isChecked():
            bounds = self._loop_bar.get_segment_bounds(self._loop_bar._active_segment)
            if bounds and ms >= bounds[1]:
                self._sync_seek(bounds[0])
                return

        self._loop_bar.set_playhead(ms)
        self._seek_slider.blockSignals(True)
        self._seek_slider.setValue(pos_ms)
        self._seek_slider.blockSignals(False)
        dur = float(self._players[0][0].duration())
        self._time_lbl.setText(f"{_ms_to_str(ms)} / {_ms_to_str(dur)}")

        ratio = (ms / dur) if dur > 0 else 0.0
        for row in self._track_rows:
            row.set_playhead_ratio(ratio)

    def _on_duration(self, dur_ms: int):
        self._seek_slider.setRange(0, dur_ms)
        self._loop_bar.set_total(float(dur_ms))

    def _on_play_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("⏸" if playing else "▶")

    def _on_media_status(self, status):
        print(f"[video] mediaStatus → {status}")
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            has_video = self._players[0][0].hasVideo()
            print(f"[video] LoadedMedia: hasVideo={has_video}")
            self._video_btn.setEnabled(has_video)
            if self._pending_seek_ms > 0:
                QTimer.singleShot(100, lambda: self._sync_seek(self._pending_seek_ms))
                self._pending_seek_ms = 0.0
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop_btn.isChecked():
                bounds = self._loop_bar.get_segment_bounds(self._loop_bar._active_segment)
                start = bounds[0] if bounds else 0.0
                self._sync_seek(start)
                self._sync_play()
            else:
                self._stop()

    def _on_seek_released(self):
        self._dragging = False
        self._sync_seek(float(self._seek_slider.value()))

    # ── video window ──────────────────────────────────────────────────────────

    def _show_video_window(self):
        print("[video] _show_video_window called")
        self._video_window.show()
        self._video_window.raise_()
        QTimer.singleShot(100, self._attach_video_output)

    def _attach_video_output(self):
        print(f"[video] _attach_video_output: players={len(self._players)}")
        if self._players:
            p = self._players[0][0]
            print(f"[video] calling setVideoOutput, playbackState={p.playbackState()}, mediaStatus={p.mediaStatus()}, hasVideo={p.hasVideo()}")
            p.setVideoOutput(self._video_window.video_widget)
            print("[video] setVideoOutput done")

    # ── loop / segment ────────────────────────────────────────────────────────

    def _on_loop_toggled(self, on: bool):
        self._loop_bar.set_loop_active(on)

    def _on_segment_selected(self, seg_idx: int):
        self._loop_bar.set_active_segment(seg_idx)

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

    # ── track event handlers ──────────────────────────────────────────────────

    def _on_mute(self, track_idx: int, muted: bool):
        self._tracks[track_idx].muted = muted
        self._apply_volume(track_idx)

    def _on_volume_changed(self, track_idx: int, vol: float):
        self._tracks[track_idx].volume = vol
        self._apply_volume(track_idx)

    def _on_speed(self, value: int):
        rate = value / 100.0
        self._speed_lbl.setText(f"{rate:.2f}×")
        self._sync_rate(rate)

    def _on_master_volume(self, value: int):
        self._master_vol_lbl.setText(f"{value}%")
        self._apply_all_volumes()

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
        reply = QMessageBox.question(
            self, "Remove Track", f"Remove \"{name}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        p, _ = self._players[track_idx]
        p.stop()
        p.setSource(QUrl())
        del self._players[track_idx]
        del self._tracks[track_idx]
        row = self._track_rows.pop(track_idx)
        row.cleanup()
        self._tracks_layout.removeWidget(row)
        row.deleteLater()
        for i, r in enumerate(self._track_rows):
            r._idx = i
        if self._solo_track == track_idx:
            for i, r in enumerate(self._track_rows):
                r.set_solo_active(False)
            self._solo_track = -1
            self._pre_solo_mutes = []
        elif self._solo_track > track_idx:
            self._solo_track -= 1

    def _on_track_renamed(self, track_idx: int, new_name: str):
        self._tracks[track_idx].name = new_name

    def _on_show_in_finder(self, track_idx: int):
        file_path = self._tracks[track_idx].file
        subprocess.run(["open", "-R", file_path])

    # ── pitch shift (via dialog) ──────────────────────────────────────────────

    def _on_pitch_shift_requested(self, track_idx: int):
        data = self._tracks[track_idx]
        dlg = PitchDialog(data.source_file, data.pitch_baked, parent=self)
        dlg.applied.connect(lambda path, st, i=track_idx: self._reload_track_source(i, path, st))
        dlg.exec()

    def _reload_track_source(self, track_idx: int, new_path: str, pitch_baked: int):
        data = self._tracks[track_idx]
        data.file = new_path
        data.pitch_baked = pitch_baked
        pos = self._current_pos()
        was_playing = self._is_playing()
        player = self._players[track_idx][0]
        player.setSource(QUrl.fromLocalFile(new_path))
        player.setPosition(int(pos))
        if was_playing:
            player.play()

    # ── stem split (via dialog) ───────────────────────────────────────────────

    def _on_split_requested(self, track_idx: int):
        data = self._tracks[track_idx]
        out_dir = str(
            Path(data.source_file).parent / (Path(data.source_file).stem + "_stems")
        )
        dlg = SplitDialog(data.source_file, out_dir, parent=self)
        dlg.finished_stems.connect(self._on_stems_done)
        dlg.exec()

    def _on_stems_done(self, stems: dict):
        self.statusBar().showMessage(f"Stems ready: {', '.join(stems.keys())}", 5000)
        for name in _STEM_ORDER:
            path = stems.get(name)
            if not path:
                continue
            idx = len(self._tracks)
            data = TrackData(
                name=name.capitalize(),
                file=path,
                source_file=path,
                color=track_color(idx),
            )
            self._add_track(data, is_source=False)

    # ── download ──────────────────────────────────────────────────────────────

    def _open_downloader(self):
        start_dir = self._prefs.get("last_audio_dir", str(Path.home() / "Downloads"))
        dlg = DownloadDialog(start_dir=start_dir, parent=self)
        dlg.file_ready.connect(self._on_download_done)
        dlg.exec()

    def _on_download_done(self, path: str):
        self._prefs["last_audio_dir"] = str(Path(path).parent)
        self._open_source_file(path)

    # ── project ───────────────────────────────────────────────────────────────

    def _new_project(self):
        if self._tracks:
            reply = QMessageBox.question(
                self, "New Project", "Close the current project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._clear_tracks()
        self._current_project = None
        self.setWindowTitle("mutr")

    def _open_source_file(self, path: str = ""):
        if not path:
            start_dir = self._prefs.get("last_audio_dir", "")
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Audio / Video File", start_dir,
                "Audio/Video (*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.flac *.m4a *.ogg)",
            )
        if not path:
            return
        if self._tracks:
            reply = QMessageBox.question(
                self, "Open File", "Close the current project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._clear_tracks()
        self._prefs["last_audio_dir"] = str(Path(path).parent)
        data = TrackData(name="Full Mix", file=path, source_file=path, color=track_color(0))
        self._add_track(data, is_source=True)
        self._update_recent(path)

    def _add_track_clicked(self):
        start_dir = self._prefs.get("last_audio_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Add Audio / Video Track", start_dir,
            "Audio/Video (*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.flac *.m4a *.ogg)",
        )
        if not path:
            return
        self._prefs["last_audio_dir"] = str(Path(path).parent)
        idx = len(self._tracks)
        name = Path(path).stem
        data = TrackData(name=name, file=path, source_file=path, color=track_color(idx))
        self._add_track(data)

    def _save_project(self):
        if self._current_project is None:
            self._save_project_as()
        else:
            self._write_project(self._current_project)

    def _save_project_as(self):
        start_dir = self._prefs.get("last_project_dir", "")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", start_dir, "mutr project (*.mutrproj)"
        )
        if not path:
            return
        if not path.endswith(".mutrproj"):
            path += ".mutrproj"
        self._current_project = Path(path)
        self._prefs["last_project_dir"] = str(Path(path).parent)
        self._write_project(self._current_project)
        self._update_recent(path)
        self.setWindowTitle(f"mutr — {Path(path).name}")

    def _write_project(self, path: Path):
        state = {
            "version": 1,
            "tracks": [t.to_dict() for t in self._tracks],
            "markers": list(self._loop_bar._markers),
            "active_segment": self._loop_bar._active_segment,
            "loop_enabled": self._loop_btn.isChecked(),
            "speed": self._speed_slider.value(),
            "master_volume": self._master_vol.value(),
            "position_ms": self._current_pos(),
        }
        try:
            save_project(path, state)
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

        for i, t in enumerate(state["tracks"]):
            if not Path(t["file"]).exists():
                QMessageBox.warning(self, "Missing file",
                    f"Track file not found:\n{t['file']}\nSkipping.")
                continue
            data = TrackData.from_dict(t, track_color(i))
            self._add_track(data, is_source=(i == 0))

        if state.get("markers"):
            self._loop_bar.set_markers(state["markers"])
        if state.get("active_segment", -1) >= 0:
            self._loop_bar.set_active_segment(state["active_segment"])

        self._speed_slider.setValue(state.get("speed", 100))
        self._master_vol.setValue(state.get("master_volume", 80))

        if state.get("loop_enabled", False):
            self._loop_btn.setChecked(True)
            self._loop_bar.set_loop_active(True)

        pos = state.get("position_ms", 0.0)
        if pos > 0:
            self._pending_seek_ms = pos

        self._prefs["last_project_dir"] = str(Path(path).parent)
        self._update_recent(path)
        self.setWindowTitle(f"mutr — {Path(path).name}")

    # ── prefs / recent ────────────────────────────────────────────────────────

    def _update_recent(self, path: str):
        update_recent(self._prefs, path)
        save_prefs(self._prefs)
        self._refresh_recent_menu()

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
            elif p.suffix == ".mutrproj":
                act.triggered.connect(lambda checked, p=path: self._open_project(p))
            else:
                act.triggered.connect(lambda checked, p=path: self._open_source_file(p))

    # ── help ──────────────────────────────────────────────────────────────────

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)
        rows = [
            ("Space", "Play / Pause"),
            ("L", "Toggle loop on selected segment"),
            ("← →", "Seek ±5 seconds"),
            ("↑ ↓", "Previous / next segment"),
            ("D", "Delete nearest marker"),
            ("Double-click loop bar", "Add marker (snaps to second)"),
            ("Double-click marker", "Remove that marker"),
            ("Drag marker", "Move marker"),
            ("Right-click main track", "Pitch Shift… / Split Stems…"),
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

    def closeEvent(self, event):
        save_prefs(self._prefs)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.showMaximized()

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.endswith(".mutrproj"):
            QTimer.singleShot(0, lambda: win._open_project(arg))
        else:
            QTimer.singleShot(0, lambda: win._open_source_file(arg))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
