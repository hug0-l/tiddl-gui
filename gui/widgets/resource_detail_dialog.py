from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import aiohttp
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tiddl.core.api.models.base import AlbumItems, PlaylistItems
from tiddl.core.api.models.resources import Album, Playlist, Track

if TYPE_CHECKING:
    from gui.client import AsyncTidalClient


class ResourceDetailDialog(QDialog):
    """Dialog showing album/playlist detail with track selection."""

    def __init__(
        self,
        client: AsyncTidalClient,
        data: dict,
        resource_type: str,
        cover_id: str | None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._client = client
        self._data = data
        self._resource_type = resource_type
        self._cover_id = cover_id
        self._track_items: list[Track] = []
        self._selected_tracks: list[tuple[str, str]] = []

        self.setWindowTitle(
            "專輯預覽" if resource_type == "album" else "播放清單預覽"
        )
        self.setMinimumSize(700, 500)
        self._build_ui()
        self._populate_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Top section: cover + info
        top_row = QHBoxLayout()

        self._cover_label = QLabel()
        self._cover_label.setFixedSize(200, 200)
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_label.setStyleSheet(
            "QLabel { background-color: #333; border: 1px solid #555; }"
        )
        top_row.addWidget(self._cover_label)

        info_layout = QVBoxLayout()
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self._title_label.setWordWrap(True)
        info_layout.addWidget(self._title_label)

        self._artist_label = QLabel()
        self._artist_label.setStyleSheet("font-size: 13px;")
        info_layout.addWidget(self._artist_label)

        self._meta_label = QLabel()
        self._meta_label.setStyleSheet("color: #888;")
        info_layout.addWidget(self._meta_label)

        info_layout.addStretch()
        top_row.addLayout(info_layout, 1)
        layout.addLayout(top_row)

        # Track table
        layout.addWidget(QLabel("曲目："))
        self._track_table = QTableWidget(0, 4)
        self._track_table.setHorizontalHeaderLabels(["✔", "#", "曲名", "長度"])
        self._track_table.horizontalHeader().setStretchLastSection(True)
        self._track_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._track_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        layout.addWidget(self._track_table)

        # Bottom buttons
        btn_row = QHBoxLayout()
        self._download_btn = QPushButton("下載選取的曲目 (0)")
        self._download_btn.clicked.connect(self._on_download)
        btn_row.addWidget(self._download_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _populate_data(self) -> None:
        if self._resource_type == "album":
            album: Album = self._data["album"]
            tracks: AlbumItems = self._data["tracks"]
            self._title_label.setText(album.title)
            artist_name = (
                album.artist.name
                if album.artist
                else (album.artists[0].name if album.artists else "未知")
            )
            self._artist_label.setText(artist_name)
            duration_min = album.duration // 60
            year = album.releaseDate.year if album.releaseDate else "?"
            self._meta_label.setText(
                f"{year} · {album.numberOfTracks} 首曲目 · {duration_min} 分鐘"
            )
            self._track_items = [
                item.item for item in tracks.items if item.type == "track"
            ]
        else:
            playlist: Playlist = self._data["playlist"]
            tracks_pi: PlaylistItems = self._data["tracks"]
            self._title_label.setText(playlist.title)
            duration_min = playlist.duration // 60
            self._meta_label.setText(
                f"{playlist.numberOfTracks} 首曲目 · {duration_min} 分鐘"
            )
            self._track_items = [
                item.item for item in tracks_pi.items if item.type == "track"
            ]

        self._fill_track_table()

        if self._cover_id:
            asyncio.ensure_future(self._fetch_cover(self._cover_id))

    def _fill_track_table(self) -> None:
        self._track_table.setRowCount(len(self._track_items))
        for i, track in enumerate(self._track_items):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(True)
            cb.stateChanged.connect(self._update_download_btn)
            self._track_table.setCellWidget(i, 0, cb)

            # Track number
            num_item = QTableWidgetItem(str(track.trackNumber))
            self._track_table.setItem(i, 1, num_item)

            # Title with artist prefix
            artist_name = (
                track.artist.name
                if track.artist
                else (track.artists[0].name if track.artists else "")
            )
            title = track.title
            display = f"{artist_name} - {title}" if artist_name else title
            title_item = QTableWidgetItem(display)
            self._track_table.setItem(i, 2, title_item)

            # Duration
            mins = track.duration // 60
            secs = track.duration % 60
            dur_item = QTableWidgetItem(f"{mins}:{secs:02d}")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._track_table.setItem(i, 3, dur_item)

        self._update_download_btn()

    def _update_download_btn(self) -> None:
        count = 0
        for i in range(self._track_table.rowCount()):
            cb = self._track_table.cellWidget(i, 0)
            if cb and cb.isChecked():
                count += 1
        self._download_btn.setText(f"下載選取的曲目 ({count})")

    async def _fetch_cover(self, cover_id: str) -> None:
        url = (
            f"https://resources.tidal.com/images/"
            f"{cover_id.replace('-', '/')}/640x640.jpg"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        pixmap = QPixmap()
                        pixmap.loadFromData(data)
                        if not pixmap.isNull():
                            self._cover_label.setPixmap(
                                pixmap.scaled(
                                    200,
                                    200,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation,
                                )
                            )
        except Exception:
            pass  # Cover is optional, leave placeholder

    def _on_download(self) -> None:
        selected: list[tuple[str, str]] = []
        for i in range(len(self._track_items)):
            cb = self._track_table.cellWidget(i, 0)
            if cb and cb.isChecked():
                selected.append(("track", str(self._track_items[i].id)))
        self._selected_tracks = selected
        self.accept()

    @property
    def selected_tracks(self) -> list[tuple[str, str]]:
        return self._selected_tracks
