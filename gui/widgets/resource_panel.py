from __future__ import annotations

from typing import TYPE_CHECKING, Optional, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from tiddl.cli.utils.resource import TidalResource, ResourceTypeLiteral
from tiddl.core.api.models.base import Search, SearchArtist
from tiddl.core.api.models.resources import Track, Album, Playlist, Video

if TYPE_CHECKING:
    from gui.client import (
        AsyncTidalClient,
        ErrorInfo,
    )


_RESOURCE_TYPES: list[ResourceTypeLiteral] = [
    "track",
    "video",
    "album",
    "playlist",
    "artist",
]


def _display_name(item) -> str:
    """Same display logic as CLI search.py."""
    if isinstance(item, SearchArtist):
        return item.name
    elif isinstance(item, Video):
        return (
            f"{item.artist or item.artists[0].name or ''}"
            f" - {item.title}"
        )
    elif isinstance(item, (Track, Album)):
        return (
            f"{item.artist or item.artists[0].name or ''}"
            f" - {item.title}"
            f" [{'/'.join(item.audioModes)}]"
        )
    elif isinstance(item, Playlist):
        return item.title
    return str(item)


def _display_id(item) -> str:
    if isinstance(item, Playlist):
        return item.uuid
    return str(item.id)


class _UrlParseResult:
    """Result of parsing a single URL line."""

    def __init__(
        self,
        resource: TidalResource | None = None,
        error: str | None = None,
    ):
        self.resource = resource
        self.error = error


class ResourcePanel(QWidget):
    resources_changed = Signal()

    _FAV_TYPE_MAPPING = {
        "track": "TRACK",
        "video": "VIDEO",
        "album": "ALBUM",
        "playlist": "PLAYLIST",
        "artist": "ARTIST",
    }

    def __init__(
        self,
        client: AsyncTidalClient,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._client = client
        self._resources: list[TidalResource] = []
        self._parsed_results: list[_UrlParseResult] = []
        self._search_results: list[tuple[str, str, str]] = []
        self._last_favorites_data: dict | None = None

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._build_url_tab()
        self._build_search_tab()
        self._build_favorites_tab()
        self._build_queue_tab()
        layout.addWidget(self._tabs)

        self._client.search_results.connect(self._on_search_results)
        self._client.search_error.connect(self._on_search_error)
        self._client.favorites_loaded.connect(self._on_favorites_loaded)
        self._client.favorites_error.connect(self._on_favorites_error)

    def get_resources(self) -> list[TidalResource]:
        """Return a copy of the current resource queue."""
        return list(self._resources)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_url_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel("URL（每行一個）："))
        self._url_input = QPlainTextEdit()
        self._url_input.setPlaceholderText(
            "輸入 Tidal URL，每行一個\n"
            "例如：\n"
            "https://listen.tidal.com/track/12345678\n"
            "album/87654321"
        )
        layout.addWidget(self._url_input)

        parse_btn = QPushButton("解析")
        parse_btn.clicked.connect(self._parse_urls)
        layout.addWidget(parse_btn)

        layout.addWidget(QLabel("解析結果："))
        self._url_results_table = QTableWidget(0, 3)
        self._url_results_table.setHorizontalHeaderLabels(["Type", "ID", "URL"])
        self._url_results_table.horizontalHeader().setStretchLastSection(True)
        self._url_results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._url_results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        layout.addWidget(self._url_results_table)

        self._url_error_label = QLabel()
        self._url_error_label.setStyleSheet("color: red;")
        self._url_error_label.setWordWrap(True)
        layout.addWidget(self._url_error_label)

        add_url_btn = QPushButton("加入佇列")
        add_url_btn.clicked.connect(self._add_parsed_urls_to_queue)
        layout.addWidget(add_url_btn)

        self._tabs.addTab(tab, "URL 輸入")

    def _build_search_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search input row
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("輸入搜尋關鍵字…")
        # Ctrl+Return triggers search
        ctrl_return = QShortcut(QKeySequence("Ctrl+Return"), self._search_input)
        ctrl_return.activated.connect(self._do_search)
        search_row.addWidget(self._search_input, 1)

        self._search_num = QSpinBox()
        self._search_num.setMinimum(1)
        self._search_num.setMaximum(10)
        self._search_num.setValue(3)
        search_row.addWidget(QLabel("顯示："))
        search_row.addWidget(self._search_num)

        search_btn = QPushButton("搜尋")
        search_btn.clicked.connect(self._do_search)
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        # Resource type checkboxes
        type_row = QHBoxLayout()
        self._search_type_checks: dict[str, QCheckBox] = {}
        for rt in _RESOURCE_TYPES:
            cb = QCheckBox(rt.title())
            cb.setChecked(True)
            self._search_type_checks[rt] = cb
            type_row.addWidget(cb)
        layout.addLayout(type_row)

        # Top-hit auto-select
        top_hit_row = QHBoxLayout()
        self._search_top_hit = QCheckBox("自動選取 Top Hit")
        top_hit_row.addWidget(self._search_top_hit)
        top_hit_row.addStretch()
        layout.addLayout(top_hit_row)

        # Results table
        self._search_table = QTableWidget(0, 5)
        self._search_table.setHorizontalHeaderLabels(
            ["#", "Type", "Title", "Artist", "ID"]
        )
        self._search_table.horizontalHeader().setStretchLastSection(True)
        self._search_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._search_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._search_table.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )

        layout.addWidget(self._search_table)
        self._search_table.cellDoubleClicked.connect(self._add_search_to_queue)

        self._search_error_label = QLabel()
        self._search_error_label.setStyleSheet("color: red;")
        self._search_error_label.setWordWrap(True)
        layout.addWidget(self._search_error_label)

        add_search_btn = QPushButton("加入佇列")
        add_search_btn.clicked.connect(self._add_search_to_queue)
        layout.addWidget(add_search_btn)

        self._tabs.addTab(tab, "搜尋")

    def _build_favorites_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Resource type checkboxes
        type_layout = QHBoxLayout()
        self._fav_type_checks: dict[str, QCheckBox] = {}
        for rt in _RESOURCE_TYPES:
            cb = QCheckBox(rt.title())
            cb.setChecked(True)
            self._fav_type_checks[rt] = cb
            type_layout.addWidget(cb)
        layout.addLayout(type_layout)

        load_fav_btn = QPushButton("載入最愛")
        load_fav_btn.clicked.connect(self._load_favorites)
        layout.addWidget(load_fav_btn)

        self._fav_stats_label = QLabel()
        self._fav_stats_label.setWordWrap(True)
        layout.addWidget(self._fav_stats_label)

        self._fav_error_label = QLabel()
        self._fav_error_label.setStyleSheet("color: red;")
        self._fav_error_label.setWordWrap(True)
        layout.addWidget(self._fav_error_label)

        add_fav_btn = QPushButton("全部加入佇列")
        add_fav_btn.clicked.connect(self._add_favorites_to_queue)
        layout.addWidget(add_fav_btn)

        layout.addStretch()

        self._tabs.addTab(tab, "我的最愛")

    def _build_queue_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._queue_table = QTableWidget(0, 4)
        self._queue_table.setHorizontalHeaderLabels(["Type", "ID", "URL", ""])
        self._queue_table.horizontalHeader().setStretchLastSection(True)
        self._queue_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._queue_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        layout.addWidget(self._queue_table)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_queue)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._tabs.addTab(tab, "下載佇列")

    # ------------------------------------------------------------------
    # URL tab
    # ------------------------------------------------------------------

    def _parse_urls(self) -> None:
        text = self._url_input.toPlainText().strip()
        if not text:
            self._url_error_label.setText("請輸入 URL")
            return

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        results: list[_UrlParseResult] = []

        for line in lines:
            try:
                resource = TidalResource.from_string(line)
                results.append(_UrlParseResult(resource=resource))
            except ValueError as e:
                results.append(_UrlParseResult(error=str(e)))

        self._url_results_table.setRowCount(len(results))
        for i, result in enumerate(results):
            if result.resource:
                self._url_results_table.setItem(
                    i, 0, QTableWidgetItem(result.resource.type)
                )
                self._url_results_table.setItem(
                    i, 1, QTableWidgetItem(result.resource.id)
                )
                self._url_results_table.setItem(
                    i, 2, QTableWidgetItem(result.resource.url)
                )
            elif result.error:
                err_item = QTableWidgetItem("錯誤")
                err_item.setForeground(Qt.GlobalColor.red)
                err_item.setToolTip(result.error)
                msg_item = QTableWidgetItem(result.error)
                msg_item.setForeground(Qt.GlobalColor.red)
                self._url_results_table.setItem(i, 0, err_item)
                self._url_results_table.setItem(i, 1, msg_item)
                self._url_results_table.setItem(i, 2, QTableWidgetItem(""))

        self._parsed_results = results
        self._url_error_label.setText("")

    def _add_parsed_urls_to_queue(self) -> None:
        added = 0
        for result in self._parsed_results:
            if result.resource:
                self._resources.append(result.resource)
                added += 1
        if added > 0:
            self._refresh_queue_table()
            self.resources_changed.emit()
            self._tabs.setCurrentIndex(3)
            self._url_error_label.setStyleSheet("color: green;")
            self._url_error_label.setText(f"✅ 已加入 {added} 個資源到佇列")
        else:
            self._url_error_label.setStyleSheet("color: red;")
            self._url_error_label.setText("沒有可加入的資源")

    # ------------------------------------------------------------------
    # Search tab
    # ------------------------------------------------------------------

    @asyncSlot()
    async def _do_search(self) -> None:
        query = self._search_input.text().strip()
        if not query:
            self._search_error_label.setText("請輸入搜尋關鍵字")
            return

        self._search_error_label.setText("")
        self._search_table.setRowCount(0)
        self._search_results = []

        selected_types = [
            rt
            for rt, cb in self._search_type_checks.items()
            if cb.isChecked()
        ]
        await self._client.search(query, selected_types or _RESOURCE_TYPES)

    def _on_search_results(self, results: Search) -> None:
        self._search_table.setRowCount(0)
        self._search_results = []
        num = self._search_num.value()
        selected_types = [
            rt
            for rt, cb in self._search_type_checks.items()
            if cb.isChecked()
        ]
        if not selected_types:
            selected_types = _RESOURCE_TYPES

        auto_added = False

        # Top-hit auto-select
        if self._search_top_hit.isChecked() and results.topHit:
            th = results.topHit
            th_type = th.type.rstrip("S").lower()
            if th_type in selected_types:
                name = _display_name(th.value)
                tid = _display_id(th.value)
                self._search_results.append((th_type, name, tid))
                try:
                    res = cast(TidalResource, TidalResource(type=th_type, id=tid))
                    self._resources.append(res)
                    auto_added = True
                except Exception:
                    pass

        # Type-to-items mapping
        type_to_items = [
            ("artist", results.artists.items),
            ("album", results.albums.items),
            ("playlist", results.playlists.items),
            ("track", results.tracks.items),
            ("video", results.videos.items),
        ]

        for rtype, items in type_to_items:
            if rtype in selected_types:
                for item in items[:num]:
                    name = _display_name(item)
                    tid = _display_id(item)
                    self._search_results.append((rtype, name, tid))

        # Populate table
        self._search_table.setRowCount(len(self._search_results))
        for i, (rtype, name, tid) in enumerate(self._search_results):
            self._search_table.setItem(
                i, 0, QTableWidgetItem(str(i + 1))
            )
            self._search_table.setItem(
                i, 1, QTableWidgetItem(rtype.title())
            )
            if " - " in name:
                artist_part, _, title_part = name.partition(" - ")
                self._search_table.setItem(
                    i, 2, QTableWidgetItem(title_part)
                )
                self._search_table.setItem(
                    i, 3, QTableWidgetItem(artist_part)
                )
            else:
                self._search_table.setItem(
                    i, 2, QTableWidgetItem(name)
                )
                self._search_table.setItem(i, 3, QTableWidgetItem(""))
            self._search_table.setItem(i, 4, QTableWidgetItem(tid))

        if auto_added:
            self._refresh_queue_table()
            self.resources_changed.emit()
            self._tabs.setCurrentIndex(3)
            self._search_error_label.setStyleSheet("color: green;")
            self._search_error_label.setText("✅ Top Hit 已自動加入佇列")

    def _on_search_error(self, error: ErrorInfo) -> None:
        self._search_error_label.setText(
            f"❌ {error.user_message}\n💡 {error.suggestion}"
        )

    def _add_search_to_queue(self) -> None:
        selected_rows = set()
        for item in self._search_table.selectedItems():
            selected_rows.add(item.row())
        if not selected_rows:
            self._search_error_label.setStyleSheet("color: red;")
            self._search_error_label.setText("請先選取要加入的搜尋結果")
            return
        added = 0
        for row in sorted(selected_rows):
            if row < len(self._search_results):
                rtype, _name, tid = self._search_results[row]
                try:
                    res = cast(
                        TidalResource, TidalResource(type=rtype, id=tid)
                    )
                    self._resources.append(res)
                    added += 1
                except Exception:
                    pass
        self._refresh_queue_table()
        self.resources_changed.emit()
        self._tabs.setCurrentIndex(3)
        self._search_error_label.setStyleSheet("color: green;")
        self._search_error_label.setText(f"✅ 已加入 {added} 個資源到佇列")

    # ------------------------------------------------------------------
    # Favorites tab
    # ------------------------------------------------------------------

    @asyncSlot()
    async def _load_favorites(self) -> None:
        self._fav_error_label.setText("")
        self._fav_stats_label.setText("載入中…")
        await self._client.get_favorites()

    def _on_favorites_loaded(self, data: dict) -> None:
        self._last_favorites_data = data
        stats_parts: list[str] = []
        for rt, cb in self._fav_type_checks.items():
            if cb.isChecked():
                key = self._FAV_TYPE_MAPPING[rt]
                count = len(data.get(key, []))
                stats_parts.append(f"{rt.title()}s: {count}")
        self._fav_stats_label.setText(" | ".join(stats_parts))

    def _on_favorites_error(self, error: ErrorInfo) -> None:
        self._fav_stats_label.setText("")
        self._fav_error_label.setText(
            f"❌ {error.user_message}\n💡 {error.suggestion}"
        )

    def _add_favorites_to_queue(self) -> None:
        if not self._last_favorites_data:
            self._fav_error_label.setText("請先載入最愛")
            return
        added = 0
        for rt, cb in self._fav_type_checks.items():
            if cb.isChecked():
                key = self._FAV_TYPE_MAPPING[rt]
                ids = self._last_favorites_data.get(key, [])
                for rid in ids:
                    try:
                        self._resources.append(
                            cast(
                                TidalResource,
                                TidalResource(type=rt, id=rid),
                            )
                        )
                        added += 1
                    except Exception:
                        pass
        if added > 0:
            self._refresh_queue_table()
            self.resources_changed.emit()
            self._tabs.setCurrentIndex(3)
            self._fav_stats_label.setText(f"✅ 已加入 {added} 個資源到佇列")

    # ------------------------------------------------------------------
    # Queue tab
    # ------------------------------------------------------------------

    def _refresh_queue_table(self) -> None:
        self._queue_table.setRowCount(len(self._resources))
        for i, res in enumerate(self._resources):
            self._queue_table.setItem(i, 0, QTableWidgetItem(res.type))
            self._queue_table.setItem(i, 1, QTableWidgetItem(res.id))
            self._queue_table.setItem(i, 2, QTableWidgetItem(res.url))
            remove_btn = QPushButton("移除")
            remove_btn.clicked.connect(
                lambda checked, row=i: self._remove_from_queue(row)
            )
            self._queue_table.setCellWidget(i, 3, remove_btn)

    def _remove_from_queue(self, row: int) -> None:
        if 0 <= row < len(self._resources):
            del self._resources[row]
            self._refresh_queue_table()
            self.resources_changed.emit()

    def _clear_queue(self) -> None:
        self._resources.clear()
        self._refresh_queue_table()
        self.resources_changed.emit()
