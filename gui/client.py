from __future__ import annotations

import asyncio
from time import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from qasync import asyncSlot

from tiddl.cli.config import APP_PATH
from tiddl.cli.utils.auth.core import load_auth_data, save_auth_data
from tiddl.cli.utils.auth.models import AuthData
from tiddl.core.api import TidalAPI, TidalClient
from tiddl.core.auth import AuthAPI
from tiddl.core.auth.exceptions import AuthClientError
from tiddl.core.utils.ffmpeg import is_ffmpeg_installed

from gui.error_handler import ErrorInfo


class AsyncTidalClient(QObject):
    auth_loaded = Signal(bool)
    ffmpeg_status = Signal(bool, str)
    login_url_ready = Signal(str, int)
    login_success = Signal(dict)
    login_error = Signal(ErrorInfo)
    login_progress = Signal(str)
    search_results = Signal(object)
    search_error = Signal(ErrorInfo)
    favorites_loaded = Signal(dict)
    favorites_error = Signal(ErrorInfo)
    api_error = Signal(ErrorInfo)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._auth_api = AuthAPI()
        self._api: TidalAPI | None = None
        self._auth_data = load_auth_data()

        self.auth_loaded.emit(self._auth_data.token is not None)

        ffmpeg_ok = is_ffmpeg_installed()
        self.ffmpeg_status.emit(ffmpeg_ok, "")

    @property
    def is_logged_in(self) -> bool:
        return self._auth_data.token is not None

    @property
    def token_expires_at(self) -> int:
        return self._auth_data.expires_at

    @property
    def username(self) -> str:
        return str(self._auth_data.user_id or "")

    @asyncSlot()
    async def check_auth_status(self) -> None:
        """Reload auth data from disk and emit auth_loaded."""
        self._auth_data = await asyncio.to_thread(load_auth_data)
        self.auth_loaded.emit(self._auth_data.token is not None)

    @asyncSlot()
    async def login(
        self, url_handler: Callable[[str, int], None] | None = None
    ) -> None:
        """Device auth flow: get device auth -> emit URL -> poll -> success/error."""
        try:
            device_auth = await asyncio.to_thread(self._auth_api.get_device_auth)
            url = device_auth.verificationUriComplete or device_auth.verificationUri
            expires_in = device_auth.expiresIn
            device_code = device_auth.deviceCode
            interval = device_auth.interval

            self.login_url_ready.emit(url, expires_in)
            if url_handler is not None:
                url_handler(url, expires_in)

            deadline = time() + expires_in
            while time() < deadline:
                try:
                    auth_response = await asyncio.to_thread(
                        self._auth_api.get_auth, device_code
                    )
                    new_auth = AuthData(
                        token=auth_response.access_token,
                        refresh_token=auth_response.refresh_token,
                        expires_at=auth_response.expires_in + int(time()),
                        user_id=str(auth_response.user_id),
                        country_code=auth_response.user.countryCode,
                    )
                    await asyncio.to_thread(save_auth_data, new_auth)
                    self._auth_data = new_auth
                    self._api = None
                    self.login_success.emit(auth_response.model_dump())
                    return
                except AuthClientError as e:
                    error_str = (e.error or "").lower()
                    if "authorization_pending" in error_str:
                        self.login_progress.emit("等待使用者授權…")
                        await asyncio.sleep(interval)
                    elif "expired_token" in error_str or "expired" in error_str:
                        self.login_error.emit(
                            ErrorInfo(
                                user_message="授權已過期",
                                technical_detail=str(e),
                                suggestion="請重新開始登入流程",
                            )
                        )
                        return
                    else:
                        self.login_error.emit(self._wrap_error(e, "登入失敗"))
                        return

            self.login_error.emit(
                ErrorInfo(
                    user_message="授權逾時",
                    technical_detail="",
                    suggestion="請重新開始登入流程",
                )
            )
        except Exception as e:
            self.login_error.emit(self._wrap_error(e, "無法啟動登入"))

    def logout(self) -> None:
        """Clear auth data and reset API."""
        empty = AuthData()
        save_auth_data(empty)
        self._auth_data = empty
        self._api = None
        self.auth_loaded.emit(False)

    @asyncSlot()
    async def search(self, query: str, types: list[str] | None = None) -> None:
        """Search Tidal and emit search_results or search_error."""
        _ = types  # kept for future type-filter support
        try:
            api = await self.get_api()
            result = await asyncio.to_thread(api.get_search, query)
            self.search_results.emit(result)
        except Exception as e:
            self.search_error.emit(self._wrap_error(e, "搜尋失敗"))

    @asyncSlot()
    async def get_favorites(self) -> None:
        """Get favorites and emit favorites_loaded or favorites_error."""
        try:
            api = await self.get_api()
            favorites = await asyncio.to_thread(api.get_favorites)
            self.favorites_loaded.emit(favorites.model_dump())
        except Exception as e:
            self.favorites_error.emit(self._wrap_error(e, "無法載入收藏"))

    @asyncSlot()
    async def refresh_token(self) -> str | None:
        """Refresh and save token, return new access token or None on failure."""
        try:
            if not self._auth_data.refresh_token:
                raise ValueError("No refresh token available")
            auth_response = await asyncio.to_thread(
                self._auth_api.refresh_token, self._auth_data.refresh_token
            )
            self._auth_data.token = auth_response.access_token
            self._auth_data.expires_at = auth_response.expires_in + int(time())
            await asyncio.to_thread(save_auth_data, self._auth_data)
            return auth_response.access_token
        except Exception as e:
            self.api_error.emit(self._wrap_error(e, "無法重新整理權杖"))
            return None

    async def get_api(self) -> TidalAPI:
        """Lazy-init TidalAPI with token refresh callback."""
        if self._api is not None:
            return self._api

        auth_data = self._auth_data
        refresh_token = auth_data.refresh_token

        def on_token_expiry() -> str | None:
            auth_response = self._auth_api.refresh_token(refresh_token)
            self._auth_data.token = auth_response.access_token
            self._auth_data.expires_at = auth_response.expires_in + int(time())
            save_auth_data(self._auth_data)
            return auth_response.access_token

        client = TidalClient(
            token=auth_data.token,
            cache_name=APP_PATH / "api_cache",
            on_token_expiry=on_token_expiry,
        )
        self._api = TidalAPI(client, auth_data.user_id, auth_data.country_code)
        return self._api

    def _wrap_error(self, exc: Exception, fallback_msg: str) -> ErrorInfo:
        """Wrap an exception into ErrorInfo with user-facing suggestion."""
        tech = f"{type(exc).__name__}: {exc}"
        msg = str(exc) if str(exc) else fallback_msg
        return ErrorInfo(
            user_message=msg,
            technical_detail=tech,
            suggestion="請檢查網路連線後重試",
        )
