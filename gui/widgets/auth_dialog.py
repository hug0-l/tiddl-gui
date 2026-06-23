from __future__ import annotations

import asyncio
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gui.client import AsyncTidalClient
from gui.error_handler import ErrorInfo


class LoginDialog(QDialog):
    login_successful = Signal(dict)

    def __init__(self, client: AsyncTidalClient, parent: Optional[QDialog] = None):
        super().__init__(parent)
        self._client = client
        self._url = ""
        self._remaining = 0
        self._login_task: asyncio.Task | None = None

        self.setWindowTitle("登入 Tidal")
        self.setMinimumWidth(480)

        self._build_ui()
        self._connect_signals()

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick)

        self._start_login()

    def _build_ui(self) -> None:
        self._instruction_label = QLabel("請在瀏覽器中完成驗證")
        self._instruction_label.setWordWrap(True)

        self._url_label = QLabel()
        self._url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self._url_label.setOpenExternalLinks(True)
        self._url_label.setWordWrap(True)

        self._open_url_btn = QPushButton("在瀏覽器中開啟")
        self._copy_url_btn = QPushButton("複製連結")

        self._countdown_label = QLabel()
        self._status_label = QLabel("等待驗證中…")

        self._action_btn = QPushButton("取消")

        layout = QVBoxLayout(self)
        layout.addWidget(self._instruction_label)

        url_row = QHBoxLayout()
        url_row.addWidget(self._url_label, 1)
        layout.addLayout(url_row)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._open_url_btn)
        btn_row.addWidget(self._copy_url_btn)
        layout.addLayout(btn_row)

        layout.addWidget(self._countdown_label)
        layout.addWidget(self._status_label)
        layout.addStretch()
        layout.addWidget(self._action_btn)

    def _connect_signals(self) -> None:
        self._open_url_btn.clicked.connect(self._open_url)
        self._copy_url_btn.clicked.connect(self._copy_url)
        self._action_btn.clicked.connect(self._on_action)

        self._client.login_url_ready.connect(self._on_url_ready)
        self._client.login_progress.connect(self._on_progress)
        self._client.login_success.connect(self._on_success)
        self._client.login_error.connect(self._on_error)

    # ---- Login flow control ----

    def _start_login(self) -> None:
        self._status_label.setText("等待驗證中…")
        self._action_btn.setText("取消")
        self._login_task = asyncio.ensure_future(self._client.login())

    def _cancel_login(self) -> None:
        self._countdown_timer.stop()
        if self._login_task is not None:
            self._login_task.cancel()
            self._login_task = None

    # ---- Client signal handlers ----

    def _on_url_ready(self, url: str, expires_in: int) -> None:
        self._url = url
        self._url_label.setText(f'<a href="{url}">{url}</a>')
        self._remaining = expires_in
        self._update_countdown()
        self._countdown_timer.start()

    def _on_progress(self, message: str) -> None:
        self._status_label.setText(message)

    def _on_success(self, auth_data: dict) -> None:
        self._countdown_timer.stop()
        self.login_successful.emit(auth_data)
        self.accept()

    def _on_error(self, error: ErrorInfo) -> None:
        self._countdown_timer.stop()
        self._remaining = 0
        self._update_countdown()
        self._status_label.setText(error.user_message)
        self._action_btn.setText("重試")

    # ---- Countdown ----

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining < 0:
            self._remaining = 0
            self._countdown_timer.stop()
        self._update_countdown()

    def _update_countdown(self) -> None:
        minutes = self._remaining // 60
        seconds = self._remaining % 60
        self._countdown_label.setText(f"剩餘時間: {minutes}:{seconds:02d}")

    # ---- Button actions ----

    def _open_url(self) -> None:
        if self._url:
            QDesktopServices.openUrl(self._url)

    def _copy_url(self) -> None:
        if self._url:
            QApplication.clipboard().setText(self._url)

    def _on_action(self) -> None:
        if self._action_btn.text() == "取消":
            self._cancel_login()
            self.reject()
        else:
            self._start_login()

    def reject(self) -> None:
        self._cancel_login()
        super().reject()


def show_logout_confirmation(client: AsyncTidalClient, parent=None) -> bool:
    """Show logout confirmation dialog. Returns True if user confirmed logout."""
    reply = QMessageBox.question(
        parent,
        "登出",
        "確定要登出嗎？登出後需重新登入才能下載。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        client.logout()
        return True
    return False
