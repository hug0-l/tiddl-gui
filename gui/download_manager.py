from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from tiddl.cli.commands.download.downloader import Downloader
from tiddl.cli.config import (
    CONFIG,
    Config,
)
from tiddl.cli.utils.resource import TidalResource
from tiddl.core.resolver import ResourceResolver
from tiddl.core.api import TidalAPI
from tiddl.core.api.models import Track, Video

if TYPE_CHECKING:
    from gui.client import AsyncTidalClient

log = logging.getLogger(__name__)


class _TaskStub:
    """Minimal substitute for rich.progress.Task used by Downloader."""

    def __init__(self, description: str = ""):
        self.description = description


class _ItemDisplay:
    """Helper to build display names for Track/Video items."""

    @staticmethod
    def name(item: Track | Video) -> str:
        artist = ""
        if isinstance(item, Track):
            artist = item.artist.name if item.artist else (item.artists[0].name if item.artists else "")
        elif isinstance(item, Video):
            artist = item.artist.name if item.artist else (item.artists[0].name if item.artists else "")
        return f"{artist} - {item.title}" if artist else item.title


class GuiOutput:
    """Bridge between Downloader RichOutput calls and DownloadManager signals.

    Implements enough of the RichOutput interface (download_start,
    download_advance, download_finish, show_item_result, console.print)
    so that Downloader can use it directly.
    """

    def __init__(self, manager: DownloadManager, track_id: str):
        self._manager = manager
        self._track_id = track_id
        self._task_id_counter = 0
        self._task_bytes: dict[int, float] = {}

    def _next_id(self) -> int:
        tid = self._task_id_counter
        self._task_id_counter += 1
        return tid

    def download_start(self, description: str) -> int:
        tid = self._next_id()
        self._task_bytes[tid] = 0
        return tid

    def download_advance(self, task_id: int, size: float) -> None:
        self._task_bytes[task_id] = self._task_bytes.get(task_id, 0) + size
        downloaded = self._task_bytes[task_id]
        self._manager.download_progress.emit(
            self._track_id, float(downloaded), float(0)
        )

    def download_finish(self, task_id: int):
        self._task_bytes.pop(task_id, None)
        return _TaskStub()

    def show_item_result(
        self, result_message: str, item_description: str, item_path: Path | None
    ) -> None:
        if not self._manager._cancelled.is_set():
            success = "Error" not in result_message
            if success and item_path:
                self._manager.download_complete.emit(
                    self._track_id, True, str(item_path)
                )
            else:
                err_msg = result_message.strip("[]")
                self._manager.download_complete.emit(
                    self._track_id, False, err_msg
                )

    @property
    def console(self):
        return self

    def print(self, *args: Any, **kwargs: Any) -> None:
        text = " ".join(str(a) for a in args)
        clean = re.sub(r"\[/?\w+(?:=[^\]]*)?\]", "", text)
        self._manager.status_update.emit(clean)


class DownloadManager(QObject):
    """Manages async download of Tidal resources via Downloader.

    Signals
    -------
    track_added : resource_id, track_id, track_title
        Emitted when a new track is resolved from a resource.
    download_progress : track_id, bytes_downloaded, total_bytes
    download_complete : track_id, success, file_path_or_error
    all_downloads_complete
    status_update : plain-text status message
    """

    track_added = Signal(str, str, str)
    download_progress = Signal(str, float, float)
    download_complete = Signal(str, bool, str)
    all_downloads_complete = Signal()
    status_update = Signal(str)

    def __init__(
        self,
        client: AsyncTidalClient,
        config: Config,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._client = client
        self._config = config
        self._cancelled = asyncio.Event()
        self._paused = asyncio.Event()
        self._paused.set()
        self._active = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_download(
        self, resources: list[TidalResource], options: dict
    ) -> None:
        """Start downloading resources. Reuses Downloader internally.

        Parameters
        ----------
        resources : list of TidalResource
            Resources to download.
        options : dict
            Override options (track_quality, video_quality, download_path,
            scan_path, skip_existing, threads_count, videos_filter,
            atmos_filter).
        """
        if self._active:
            self.status_update.emit("已有下載任務在進行中")
            return

        self._active = True
        self._cancelled.clear()
        self._paused.set()

        self.status_update.emit("正在準備下載…")

        api = await self._client.get_api()

        downloader_opts = self._build_downloader_options(options)
        downloader = Downloader(
            tidal_api=api,
            threads_count=downloader_opts["threads_count"],
            rich_output=GuiOutput(self, ""),
            track_quality=downloader_opts["track_quality"],
            video_quality=downloader_opts["video_quality"],
            videos_filter=downloader_opts["videos_filter"],
            skip_existing=downloader_opts["skip_existing"],
            download_path=downloader_opts["download_path"],
            scan_path=downloader_opts["scan_path"],
            match_existing_path_case=downloader_opts.get(
                "match_existing_path_case", False
            ),
            dolby_atmos_filter=downloader_opts["atmos_filter"],
        )

        try:
            for resource in resources:
                if self._cancelled.is_set():
                    break
                await self._paused.wait()
                await self._handle_resource(api, downloader, resource, options)
        except Exception as exc:
            self.status_update.emit(f"下載任務異常：{exc}")
        finally:
            self._active = False
            self.all_downloads_complete.emit()

    def cancel(self, resource_id: str) -> None:
        """Cancel the current download batch."""
        self._cancelled.set()
        self.status_update.emit("正在取消下載…")

    def pause(self) -> None:
        """Pause all downloads."""
        self._paused.clear()
        self.status_update.emit("下載已暫停")

    def resume(self) -> None:
        """Resume paused downloads."""
        self._paused.set()
        self.status_update.emit("下載已恢復")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_downloader_options(self, options: dict) -> dict:
        return {
            "track_quality": options.get(
                "track_quality", CONFIG.download.track_quality
            ),
            "video_quality": options.get(
                "video_quality", CONFIG.download.video_quality
            ),
            "videos_filter": options.get(
                "videos_filter", CONFIG.download.videos_filter
            ),
            "atmos_filter": options.get(
                "atmos_filter", CONFIG.download.atmos_filter
            ),
            "skip_existing": options.get(
                "skip_existing", CONFIG.download.skip_existing
            ),
            "threads_count": options.get(
                "threads_count", CONFIG.download.threads_count
            ),
            "download_path": Path(
                options.get("download_path", CONFIG.download.download_path)
            ),
            "scan_path": Path(
                options.get("scan_path", CONFIG.download.scan_path)
            ),
            "match_existing_path_case": options.get(
                "match_existing_path_case",
                CONFIG.download.match_existing_path_case,
            ),
        }

    async def _handle_resource(
        self,
        api: TidalAPI,
        downloader: Downloader,
        resource: TidalResource,
        options: dict,
    ) -> None:
        """Resolve one TidalResource to items and feed them to Downloader."""
        if self._cancelled.is_set():
            return

        resolver = ResourceResolver(api=api)

        async for item, file_path in resolver.resolve(resource, options):
            if self._cancelled.is_set():
                return
            await self._paused.wait()

            track_id = f"{resource.id}/{item.id}"
            track_title = _ItemDisplay.name(item)
            self.track_added.emit(resource.id, track_id, track_title)

            downloader.rich_output = GuiOutput(self, track_id)

            _dl_path, _was_downloaded = await downloader.download(
                item=item, file_path=file_path
            )
