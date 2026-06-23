from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from typing import Optional

from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QWidget,
)

from tiddl.core.api.exceptions import ApiError
from tiddl.core.auth.exceptions import AuthClientError


@dataclass
class ErrorInfo:
    user_message: str
    technical_detail: str
    suggestion: str


def show_error_dialog(parent: Optional[QWidget], error: ErrorInfo) -> None:
    """Show QMessageBox with error details. Has 'Copy Details' button."""
    msg_box = QMessageBox(parent)
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("錯誤")
    msg_box.setText(error.user_message)
    msg_box.setInformativeText(f"💡 {error.suggestion}")
    msg_box.setDetailedText(error.technical_detail)

    copy_btn = msg_box.addButton("複製詳細資訊", QMessageBox.ButtonRole.ActionRole)
    msg_box.addButton(QMessageBox.StandardButton.Ok)

    def _copy():
        QApplication.clipboard().setText(
            f"使用者訊息: {error.user_message}\n"
            f"技術細節: {error.technical_detail}\n"
            f"建議: {error.suggestion}"
        )

    copy_btn.clicked.connect(_copy)
    msg_box.exec()


def show_warning_notification(parent: Optional[QWidget], message: str) -> None:
    """Show non-blocking warning in a dialog."""
    msg_box = QMessageBox(parent)
    msg_box.setIcon(QMessageBox.Icon.Warning)
    msg_box.setWindowTitle("提示")
    msg_box.setText(message)
    msg_box.addButton(QMessageBox.StandardButton.Ok)
    msg_box.exec()


def handle_api_error(parent: Optional[QWidget], error: Exception) -> ErrorInfo:
    """Convert ApiError / AuthClientError / aiohttp / other exceptions to ErrorInfo."""
    if isinstance(error, ApiError):
        user_msg = error.user_message
        tech = (
            f"ApiError: status={error.status}, "
            f"subStatus={error.sub_status}, "
            f"message={error.user_message}"
        )
        suggestion = "請檢查資源是否存在或稍後重試"
    elif isinstance(error, AuthClientError):
        user_msg = error.error_description or error.error or "驗證失敗"
        tech = (
            f"AuthClientError: error={error.error}, "
            f"description={error.error_description}, "
            f"status={error.status}"
        )
        suggestion = "請重新登入"
    else:
        user_msg = str(error) if str(error) else "發生未知錯誤"
        tech = f"{type(error).__name__}: {error}"
        suggestion = "請檢查網路連線後重試"

    return ErrorInfo(
        user_message=user_msg,
        technical_detail=tech,
        suggestion=suggestion,
    )


def _global_excepthook(exc_type, exc_value, exc_tb) -> None:
    """Replacement for sys.excepthook that shows a friendly dialog."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    tech = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    error = ErrorInfo(
        user_message="發生未預期的錯誤",
        technical_detail=tech,
        suggestion="請重新啟動應用程式。若問題持續發生，請回報此錯誤。",
    )
    app = QApplication.instance()
    if app is not None:
        show_error_dialog(None, error)
    else:
        sys.__excepthook__(exc_type, exc_value, exc_tb)


def install_global_exception_hook() -> None:
    """Install sys.excepthook that shows ErrorInfo dialog."""
    sys.excepthook = _global_excepthook


def install_qt_exception_handler() -> None:
    """Handle Qt exceptions via sys.excepthook (covered by qasync event loop)."""
    install_global_exception_hook()
