from __future__ import annotations

import asyncio
from time import time
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from tiddl.cli.config import CONFIG
from tiddl.core.utils.ffmpeg import is_ffmpeg_installed

from gui.client import AsyncTidalClient
from gui.download_manager import DownloadManager
from gui.error_handler import (
    ErrorInfo,
    show_error_dialog,
    show_warning_notification,
)
from gui.widgets.auth_dialog import LoginDialog, show_logout_confirmation
from gui.widgets.download_panel import DownloadPanel
from gui.widgets.resource_panel import ResourcePanel
from gui.widgets.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    """Main application window integrating all panels and global features."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("tiddl-gui")
        self.resize(1100, 700)

        self._client = AsyncTidalClient(self)
        self._download_manager = DownloadManager(self._client, CONFIG, self)

        self._resource_panel = ResourcePanel(self._client, self)
        self._download_panel = DownloadPanel(self._download_manager, self)

        self._build_menu_bar()
        self._build_tool_bar()
        self._build_central_widget()
        self._build_status_bar()
        self._connect_signals()
        # Manually trigger initial state callbacks because AsyncTidalClient.__init__
        # emits auth_loaded and ffmpeg_status signals before _connect_signals is called.
        self._on_auth_loaded(self._client.is_logged_in)
        self._on_ffmpeg_status(is_ffmpeg_installed(), "")

        # Token countdown timer (every 60s)
        self._token_timer = QTimer(self)
        self._token_timer.setInterval(60000)
        self._token_timer.timeout.connect(self._update_token_status)
        self._token_timer.start()

        # Auto-refresh token on startup if logged in
        self._auto_refresh_token()

    # ------------------------------------------------------------------
    # Menu Bar
    # ------------------------------------------------------------------

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("檔案 (&F)")

        settings_action = QAction("設定... (Ctrl+,)", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("離開 (Ctrl+Q)", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Auth menu
        auth_menu = menu_bar.addMenu("驗證 (&A)")

        self._menu_login = QAction("登入", self)
        self._menu_login.triggered.connect(self._show_login_dialog)
        auth_menu.addAction(self._menu_login)

        self._menu_logout = QAction("登出", self)
        self._menu_logout.triggered.connect(self._confirm_logout)
        auth_menu.addAction(self._menu_logout)

        self._menu_refresh = QAction("重新整理權杖", self)
        self._menu_refresh.triggered.connect(self._do_refresh_token)
        auth_menu.addAction(self._menu_refresh)

        # Help menu
        help_menu = menu_bar.addMenu("說明 (&H)")

        about_action = QAction("關於 tiddl-gui", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        check_ffmpeg_action = QAction("檢查 ffmpeg", self)
        check_ffmpeg_action.triggered.connect(self._check_ffmpeg)
        help_menu.addAction(check_ffmpeg_action)

    # ------------------------------------------------------------------
    # Tool Bar
    # ------------------------------------------------------------------

    def _build_tool_bar(self) -> None:
        toolbar = QToolBar("主要工具列")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._toolbar_login_btn = QPushButton("登入")
        self._toolbar_login_btn.clicked.connect(self._on_toolbar_login_clicked)
        toolbar.addWidget(self._toolbar_login_btn)

        toolbar.addSeparator()

        settings_btn = QPushButton("設定")
        settings_btn.clicked.connect(self._open_settings)
        toolbar.addWidget(settings_btn)

        download_all_btn = QPushButton("下載全部")
        download_all_btn.clicked.connect(self._download_all)
        toolbar.addWidget(download_all_btn)

        toolbar.addSeparator()

        self._token_status_label = QLabel()
        toolbar.addWidget(self._token_status_label)

    # ------------------------------------------------------------------
    # Central Widget
    # ------------------------------------------------------------------

    def _build_central_widget(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        splitter.addWidget(self._resource_panel)
        splitter.addWidget(self._download_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self.setCentralWidget(splitter)

    # ------------------------------------------------------------------
    # Status Bar
    # ------------------------------------------------------------------

    def _build_status_bar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)

        self._login_status_label = QLabel("未登入")
        status.addWidget(self._login_status_label)

        status.addPermanentWidget(QLabel("  |  "))

        self._token_expiry_label = QLabel("")
        status.addPermanentWidget(self._token_expiry_label)

        status.addPermanentWidget(QLabel("  |  "))

        self._ffmpeg_status_label = QLabel("")
        status.addPermanentWidget(self._ffmpeg_status_label)

        status.addPermanentWidget(QLabel("  |  "))

        self._download_summary_label = QLabel("📥 0 / 0")
        status.addPermanentWidget(self._download_summary_label)

    # ------------------------------------------------------------------
    # Signal Connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        c = self._client

        c.auth_loaded.connect(self._on_auth_loaded)
        c.login_success.connect(self._on_login_success)
        c.login_error.connect(self._on_login_error)
        c.ffmpeg_status.connect(self._on_ffmpeg_status)
        c.api_error.connect(self._on_api_error)

        # DownloadManager signals for status bar summary
        dm = self._download_manager
        dm.all_downloads_complete.connect(self._update_download_summary)
        dm.download_complete.connect(self._update_download_summary)

    # ------------------------------------------------------------------
    # Auth State Handlers
    # ------------------------------------------------------------------

    def _on_auth_loaded(self, logged_in: bool) -> None:
        self._update_auth_ui(logged_in)

    def _on_login_success(self, auth_data: dict) -> None:
        self._update_auth_ui(True)
        show_warning_notification(self, "登入成功！")
        self._update_token_status()

    def _on_login_error(self, error: ErrorInfo) -> None:
        show_error_dialog(self, error)

    def _update_auth_ui(self, logged_in: bool) -> None:
        if logged_in:
            name = self._client.username
            self._toolbar_login_btn.setText(name if name else "已登入")
            self._login_status_label.setText(f"已登入: {name}" if name else "已登入")
            self._menu_login.setEnabled(False)
            self._menu_logout.setEnabled(True)
            self._menu_refresh.setEnabled(True)
        else:
            self._toolbar_login_btn.setText("登入")
            self._login_status_label.setText("未登入")
            self._menu_login.setEnabled(True)
            self._menu_logout.setEnabled(False)
            self._menu_refresh.setEnabled(False)
            self._token_expiry_label.setText("")
            self._token_status_label.setText("")

    def _on_toolbar_login_clicked(self) -> None:
        if self._client.is_logged_in:
            self._confirm_logout()
        else:
            self._show_login_dialog()

    def _confirm_logout(self) -> None:
        if show_logout_confirmation(self._client, self):
            self._update_auth_ui(False)

    # ------------------------------------------------------------------
    # Token & ffmpeg
    # ------------------------------------------------------------------

    def _update_token_status(self) -> None:
        expires_at = self._client.token_expires_at
        if expires_at == 0:
            self._token_expiry_label.setText("")
            self._token_status_label.setText("")
            return
        remaining = max(0, expires_at - int(time()))
        minutes = remaining // 60
        self._token_expiry_label.setText(f"🔑 {minutes} 分後過期")
        self._token_status_label.setText(f"🔑 {minutes}m")

    def _auto_refresh_token(self) -> None:
        if self._client.is_logged_in:
            asyncio.ensure_future(self._client.refresh_token())

    @asyncSlot()
    async def _do_refresh_token(self) -> None:
        token = await self._client.refresh_token()
        if token:
            self._update_token_status()
        # error is emitted via api_error signal

    def _on_ffmpeg_status(self, installed: bool, _version: str) -> None:
        self._ffmpeg_status_label.setText(
            "✓ ffmpeg" if installed else "✗ ffmpeg 未安裝"
        )

    def _check_ffmpeg(self) -> None:
        ok = is_ffmpeg_installed()
        QMessageBox.information(
            self,
            "ffmpeg 狀態",
            "✓ ffmpeg 已安裝" if ok else "✗ ffmpeg 未安裝",
        )

    # ------------------------------------------------------------------
    # Error Handling
    # ------------------------------------------------------------------

    def _on_api_error(self, error: ErrorInfo) -> None:
        show_error_dialog(self, error)

    # ------------------------------------------------------------------
    # Download Summary
    # ------------------------------------------------------------------

    def _update_download_summary(self, *args, **kwargs) -> None:
        """Update status bar with download progress."""
        done, total = self._download_panel.get_done_count()
        self._download_summary_label.setText(f"📥 {done} / {total}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @asyncSlot()
    async def _download_all(self) -> None:
        resources = self._resource_panel.get_resources()
        if not resources:
            show_warning_notification(self, "沒有待下載的資源")
            return
        self._download_panel.set_resources(resources)
        options = self._download_panel.build_options()
        await self._download_manager.start_download(resources, options)

    def _show_login_dialog(self) -> None:
        dialog = LoginDialog(self._client, self)
        dialog.exec()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.settings_saved.connect(self._on_settings_saved)
        dialog.exec()

    def _on_settings_saved(self) -> None:
        """Reload download options when settings change."""
        # The settings dialog already reloads CONFIG; DownloadManager
        # picks it up on next start_download via _build_downloader_options.
        self._update_ffmpeg_status()

    def _update_ffmpeg_status(self) -> None:
        ok = is_ffmpeg_installed()
        self._ffmpeg_status_label.setText(
            "✓ ffmpeg" if ok else "✗ ffmpeg 未安裝"
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "關於 tiddl-gui",
            "tiddl-gui v3.4.4a1\n\n"
            "Tidal 音樂下載圖形介面\n\n"
            "基於 tiddl CLI 構建",
        )
