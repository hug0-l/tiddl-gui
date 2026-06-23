from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QModelIndex, QPoint, Qt, QSize, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QPainter,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from tiddl.cli.config import CONFIG
from tiddl.cli.utils.resource import TidalResource

if TYPE_CHECKING:
    from gui.download_manager import DownloadManager

# Column indices
_COL_STATUS = 0
_COL_NAME = 1
_COL_TYPE = 2
_COL_PROGRESS = 3
_COL_SPEED = 4
_COL_SIZE = 5
_COL_PATH = 6

_COLUMNS = ["Status", "Name", "Type", "Progress", "Speed", "Size", "Path"]

# Custom data roles
_STATUS_ROLE = Qt.ItemDataRole.UserRole + 1
_TRACK_ID_ROLE = Qt.ItemDataRole.UserRole + 2


class ProgressBarDelegate(QStyledItemDelegate):
    """Renders a QProgressBar inside the progress column.

    Supports indeterminate mode when the display text is "...".
    """

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        progress = index.data(Qt.ItemDataRole.DisplayRole)
        if progress is None:
            super().paint(painter, option, index)
            return

        bar = QProgressBar()
        if progress == "...":
            bar.setRange(0, 0)  # indeterminate mode
        else:
            try:
                pct = float(progress)
            except (ValueError, TypeError):
                pct = 0.0
            bar.setRange(0, 100)
            bar.setValue(int(pct))
        bar.resize(option.rect.size())
        bar.setMinimumHeight(18)
        bar.setMaximumHeight(24)

        painter.save()
        painter.translate(option.rect.topLeft())
        bar.render(painter, target_offset=QPoint(0, 0))
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        return QSize(120, 24)


class DownloadPanel(QWidget):
    """Download management panel with real-time progress display.

    Layout
    ------
    - Top: Settings override row (quality selectors)
    - Middle: QTreeView with per-track progress rows
    - Bottom: Totals bar + controls
    """

    def __init__(
        self,
        download_manager: DownloadManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._manager = download_manager
        self._model = QStandardItemModel(0, len(_COLUMNS))
        self._resources: list[TidalResource] = []
        self._done_count = 0
        self._start_time: float | None = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._has_progress_started = False

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Top: settings overrides
        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("音質:"))
        self._track_quality_combo = QComboBox()
        for q in ("low", "normal", "high", "max"):
            self._track_quality_combo.addItem(q, q)
        self._track_quality_combo.setCurrentText(CONFIG.download.track_quality)
        settings_row.addWidget(self._track_quality_combo)

        settings_row.addWidget(QLabel("影片品質:"))
        self._video_quality_combo = QComboBox()
        for q in ("sd", "hd", "fhd"):
            self._video_quality_combo.addItem(q, q)
        self._video_quality_combo.setCurrentText(CONFIG.download.video_quality)
        settings_row.addWidget(self._video_quality_combo)

        settings_row.addStretch()
        layout.addLayout(settings_row)

        # Middle: QTreeView
        self._model.setHorizontalHeaderLabels(_COLUMNS)
        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setRootIsDecorated(False)
        self._tree.setItemsExpandable(False)
        self._tree.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self._tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)

        header = self._tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(
            _COL_STATUS, QHeaderView.ResizeMode.Fixed
        )
        header.resizeSection(_COL_STATUS, 36)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            _COL_TYPE, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(_COL_PROGRESS, 140)
        header.setSectionResizeMode(
            _COL_SPEED, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            _COL_SIZE, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_PATH, QHeaderView.ResizeMode.Stretch)

        self._tree.setItemDelegateForColumn(
            _COL_PROGRESS, ProgressBarDelegate(self._tree)
        )
        self._tree.clicked.connect(self._on_tree_clicked)
        layout.addWidget(self._tree, 1)

        # Bottom: total bar + count + elapsed + status
        bottom_row = QHBoxLayout()
        self._total_bar = QProgressBar()
        self._total_bar.setRange(0, 100)
        self._total_bar.setValue(0)
        bottom_row.addWidget(self._total_bar, 1)

        self._count_label = QLabel("0 / 0")
        bottom_row.addWidget(self._count_label)

        self._elapsed_label = QLabel("00:00")
        bottom_row.addWidget(self._elapsed_label)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: gray;")
        bottom_row.addWidget(self._status_label)

        layout.addLayout(bottom_row)

        # Control buttons
        ctrl_row = QHBoxLayout()
        self._start_btn = QPushButton("開始下載")
        self._start_btn.clicked.connect(self._on_start_clicked)
        ctrl_row.addWidget(self._start_btn)

        self._pause_btn = QPushButton("暫停")
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        self._pause_btn.setEnabled(False)
        ctrl_row.addWidget(self._pause_btn)

        self._cancel_btn = QPushButton("取消全部")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self._cancel_btn.setEnabled(False)
        ctrl_row.addWidget(self._cancel_btn)

        self._clear_btn = QPushButton("清除已完成")
        self._clear_btn.clicked.connect(self._clear_finished)
        ctrl_row.addWidget(self._clear_btn)

        open_btn = QPushButton("開啟輸出資料夾")
        open_btn.clicked.connect(self._open_output_folder)
        ctrl_row.addWidget(open_btn)

        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

    def _connect_signals(self) -> None:
        self._manager.track_added.connect(self._on_track_added)
        self._manager.download_progress.connect(self._on_progress)
        self._manager.download_complete.connect(self._on_complete)
        self._manager.all_downloads_complete.connect(self._on_all_complete)
        self._manager.status_update.connect(self._on_status)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_resources(self, resources: list[TidalResource]) -> None:
        """Set the list of resources to download."""
        self._resources = list(resources)
        self._reset_model()

    def get_done_count(self) -> tuple[int, int]:
        """Return (done_count, total_count) for status bar."""
        return self._done_count, self._model.rowCount()

    # ------------------------------------------------------------------
    # Model helpers
    # ------------------------------------------------------------------

    def _reset_model(self) -> None:
        self._model.removeRows(0, self._model.rowCount())
        self._done_count = 0
        self._has_progress_started = False

    def _add_track_row(self, resource_id: str, track_id: str, track_title: str) -> None:
        row_items = []
        for col in range(len(_COLUMNS)):
            item = QStandardItem()
            item.setEditable(False)
            row_items.append(item)

        row_items[_COL_STATUS].setText("⏳")
        row_items[_COL_NAME].setText(track_title)
        row_items[_COL_TYPE].setText("")
        row_items[_COL_PROGRESS].setText("0")
        row_items[_COL_SPEED].setText("")
        row_items[_COL_SIZE].setText("")
        row_items[_COL_PATH].setText("")

        row_items[_COL_STATUS].setData("waiting", _STATUS_ROLE)
        row_items[_COL_STATUS].setData(track_id, _TRACK_ID_ROLE)

        self._model.appendRow(row_items)

    def _find_row_by_track(self, track_id: str) -> int | None:
        for row in range(self._model.rowCount()):
            item = self._model.item(row, _COL_STATUS)
            if item and item.data(_TRACK_ID_ROLE) == track_id:
                return row
        return None

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_track_added(
        self, resource_id: str, track_id: str, track_title: str
    ) -> None:
        self._add_track_row(resource_id, track_id, track_title)
        self._update_total_bar()

    def _on_progress(
        self, track_id: str, downloaded: float, total: float
    ) -> None:
        row = self._find_row_by_track(track_id)
        if row is None:
            # Track row may not exist yet; create it on first progress
            self._add_track_row("", track_id, track_id)
            row = self._model.rowCount() - 1

        status_item = self._model.item(row, _COL_STATUS)
        if status_item and status_item.data(_STATUS_ROLE) == "waiting":
            status_item.setData("downloading", _STATUS_ROLE)
            status_item.setText("⬇️")

        prog_item = self._model.item(row, _COL_PROGRESS)
        if prog_item:
            if total > 0:
                pct = int(downloaded / total * 100)
                prog_item.setText(str(pct))
            else:
                prog_item.setText("...")

        # Start elapsed timer on first progress (not button click)
        if not self._has_progress_started:
            self._has_progress_started = True
            self._start_time = time.time()
            self._elapsed_timer.start(1000)

        self._update_total_bar()

    def _on_complete(
        self, track_id: str, success: bool, path_or_error: str
    ) -> None:
        row = self._find_row_by_track(track_id)
        if row is None:
            return

        status_item = self._model.item(row, _COL_STATUS)
        if not status_item:
            return

        if success:
            status_item.setText("✅")
            status_item.setData("done", _STATUS_ROLE)
            path_item = self._model.item(row, _COL_PATH)
            if path_item:
                path_item.setText(path_or_error)
            prog_item = self._model.item(row, _COL_PROGRESS)
            if prog_item:
                prog_item.setText("100")
        else:
            status_item.setText("❌")
            status_item.setData("error", _STATUS_ROLE)
            name_item = self._model.item(row, _COL_NAME)
            if name_item:
                name_item.setForeground(QBrush(QColor("#ff4444")))

        self._done_count += 1
        self._update_total_bar()

    def _on_all_complete(self) -> None:
        self._elapsed_timer.stop()
        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("暫停")
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("下載完成")

    def _on_status(self, message: str) -> None:
        self._status_label.setText(message)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    @asyncSlot()
    async def _on_start_clicked(self) -> None:
        if not self._resources:
            QMessageBox.warning(self, "提示", "沒有待下載的資源")
            return

        self._reset_model()
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)
        self._start_time = None
        self._has_progress_started = False

        options = self.build_options()
        await self._manager.start_download(self._resources, options)

    def _on_pause_clicked(self) -> None:
        if self._pause_btn.text() == "暫停":
            self._manager.pause()
            self._pause_btn.setText("繼續")
        else:
            self._manager.resume()
            self._pause_btn.setText("暫停")

    def _on_cancel_clicked(self) -> None:
        self._manager.cancel("__all__")
        self._cancel_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._start_btn.setEnabled(True)

    def _clear_finished(self) -> None:
        rows_to_remove: list[int] = []
        for row in range(self._model.rowCount()):
            item = self._model.item(row, _COL_STATUS)
            if item and item.data(_STATUS_ROLE) in ("done", "error", "exists"):
                rows_to_remove.append(row)
        for row in reversed(rows_to_remove):
            self._model.removeRow(row)
            if row < len(self._resources):
                del self._resources[row]
        self._done_count = max(0, self._done_count - len(rows_to_remove))
        self._update_total_bar()

    def _open_output_folder(self) -> None:
        path = Path(CONFIG.download.download_path)
        if path.exists():
            QDesktopServices.openUrl(path.as_uri())

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        """Open result path on click in path column."""
        if index.column() != _COL_PATH:
            return
        path_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if path_str.startswith("http"):
            return
        p = Path(path_str)
        if p.exists():
            QDesktopServices.openUrl(p.as_uri())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def build_options(self) -> dict:
        return {
            "track_quality": self._track_quality_combo.currentData(),
            "video_quality": self._video_quality_combo.currentData(),
            "videos_filter": CONFIG.download.videos_filter,
            "atmos_filter": CONFIG.download.atmos_filter,
            "skip_existing": CONFIG.download.skip_existing,
            "threads_count": CONFIG.download.threads_count,
            "download_path": CONFIG.download.download_path,
            "scan_path": CONFIG.download.scan_path,
            "match_existing_path_case": CONFIG.download.match_existing_path_case,
        }

    def _update_total_bar(self) -> None:
        total = self._model.rowCount()
        self._total_bar.setMaximum(total if total > 0 else 100)
        self._total_bar.setValue(self._done_count)
        self._count_label.setText(f"{self._done_count} / {total}")

    def _update_elapsed(self) -> None:
        if self._start_time is None:
            return
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        self._elapsed_label.setText(f"{mins:02d}:{secs:02d}")
