from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from tiddl.cli.commands.download.downloader import Downloader
from tiddl.cli.config import (
    ATMOS_FILTER_LITERAL,
    TRACK_QUALITY_LITERAL,
    VIDEO_QUALITY_LITERAL,
    VIDEOS_FILTER_LITERAL,
    CONFIG,
    Config,
)
from tiddl.cli.utils.resource import TidalResource
from tiddl.core.api import TidalAPI
from tiddl.core.api.models import Album, Track, Video
from tiddl.core.utils.format import format_template

if TYPE_CHECKING:
    from gui.client import AsyncTidalClient

log = logging.getLogger(__name__)


class _TaskStub:
    """Minimal substitute for rich.progress.Task used by Downloader."""

    def __init__(self, description: str = ""):
        self.description = description


class GuiOutput:
    """Bridge between Downloader RichOutput calls and DownloadManager signals.

    Implements enough of the RichOutput interface (download_start,
    download_advance, download_finish, show_item_result, console.print)
    so that Downloader can use it directly.
    """

    def __init__(self, manager: DownloadManager, resource_id: str):
        self._manager = manager
        self._resource_id = resource_id
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
            self._resource_id, float(downloaded), float(0)
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
                    self._resource_id, True, str(item_path)
                )
            else:
                err_msg = result_message.strip("[]")
                self._manager.download_complete.emit(
                    self._resource_id, False, err_msg
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
    download_progress : resource_id, bytes_downloaded, total_bytes
    download_complete : resource_id, success, file_path_or_error
    all_downloads_complete
    status_update : plain-text status message
    """

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
                # Create per-resource GuiOutput so signals map to correct rows
                downloader.rich_output = GuiOutput(self, resource.id)
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

    def _get_item_quality(
        self, item: Track | Video, options: dict
    ) -> str:
        track_q: TRACK_QUALITY_LITERAL = options.get(
            "track_quality", CONFIG.download.track_quality
        )
        video_q: VIDEO_QUALITY_LITERAL = options.get(
            "video_quality", CONFIG.download.video_quality
        )
        if isinstance(item, Track):
            if track_q in ("low", "normal"):
                return track_q.upper()
            if track_q == "max" and "HIRES_LOSSLESS" not in item.mediaMetadata.tags:
                return "HIGH"
            return track_q.upper()
        elif isinstance(item, Video):
            return video_q.upper()
        return track_q.upper()

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

        async def handle_item(
            item: Track | Video,
            file_path: Path,
        ) -> tuple[Path | None, Track | Video]:
            if self._cancelled.is_set():
                return None, item
            await self._paused.wait()
            dl_path, _was_downloaded = await downloader.download(
                item=item, file_path=file_path
            )
            return dl_path, item

        match resource.type:
            case "track":
                track = await asyncio.to_thread(api.get_track, resource.id)
                album = await asyncio.to_thread(api.get_album, track.album.id)
                template = options.get("template") or CONFIG.templates.track
                file_path_str = format_template(
                    template=template,
                    item=track,
                    album=album,
                    quality=self._get_item_quality(track, options),
                )
                await handle_item(track, Path(file_path_str))

            case "video":
                video = await asyncio.to_thread(api.get_video, resource.id)
                template = options.get("template") or CONFIG.templates.video
                if "{album" in template and video.album and video.album.id is not None:
                    album = await asyncio.to_thread(api.get_album, video.album.id)
                else:
                    album = None
                file_path_str = format_template(
                    template=template,
                    item=video,
                    album=album,
                    quality=self._get_item_quality(video, options),
                )
                await handle_item(video, Path(file_path_str))

            case "album":
                album = await asyncio.to_thread(api.get_album, resource.id)
                await self._download_album(
                    api, downloader, album, resource, options
                )

            case "playlist":
                playlist = await asyncio.to_thread(
                    api.get_playlist, resource.id
                )
                offset = 0
                playlist_index = 0
                template = options.get("template") or CONFIG.templates.playlist
                while True:
                    playlist_items = await asyncio.to_thread(
                        api.get_playlist_items, resource.id, offset=offset
                    )
                    for pl_item in playlist_items.items:
                        playlist_index += 1
                        if "{album" in template:
                            album = await asyncio.to_thread(
                                api.get_album, pl_item.item.album.id
                            )
                        else:
                            album = None
                        file_path_str = format_template(
                            template=template,
                            item=pl_item.item,
                            album=album,
                            playlist=playlist,
                            playlist_index=playlist_index,
                            quality=self._get_item_quality(
                                pl_item.item, options
                            ),
                        )
                        await handle_item(
                            pl_item.item, Path(file_path_str)
                        )
                    offset += playlist_items.limit
                    if offset >= playlist_items.totalNumberOfItems:
                        break

            case "artist":
                await self._download_artist(
                    api, downloader, resource, options
                )

            case "mix":
                offset = 0
                template = options.get("template") or CONFIG.templates.mix
                while True:
                    mix_items = await asyncio.to_thread(
                        api.get_mix_items, resource.id, offset=0
                    )
                    for mix_item in mix_items.items:
                        if "{album" in template:
                            album = await asyncio.to_thread(
                                api.get_album, mix_item.item.album.id
                            )
                        else:
                            album = None
                        file_path_str = format_template(
                            template=template,
                            item=mix_item.item,
                            album=album,
                            mix_id=resource.id,
                            quality=self._get_item_quality(
                                mix_item.item, options
                            ),
                        )
                        await handle_item(
                            mix_item.item, Path(file_path_str)
                        )
                    offset += mix_items.limit
                    if offset >= mix_items.totalNumberOfItems:
                        break

    async def _download_album(
        self,
        api: TidalAPI,
        downloader: Downloader,
        album: Album,
        resource: TidalResource,
        options: dict,
    ) -> None:
        offset = 0
        template = options.get("template") or CONFIG.templates.album
        while True:
            album_items = await asyncio.to_thread(
                api.get_album_items_credits, album.id, offset=offset
            )
            for album_item in album_items.items:
                if self._cancelled.is_set():
                    return
                file_path_str = format_template(
                    template=template,
                    item=album_item.item,
                    album=album,
                    quality=self._get_item_quality(album_item.item, options),
                )
                await downloader.download(
                    item=album_item.item, file_path=Path(file_path_str)
                )
            offset += album_items.limit
            if offset >= album_items.totalNumberOfItems:
                break

    async def _download_artist(
        self,
        api: TidalAPI,
        downloader: Downloader,
        resource: TidalResource,
        options: dict,
    ) -> None:
        videos_filter: VIDEOS_FILTER_LITERAL = options.get(
            "videos_filter", CONFIG.download.videos_filter
        )
        singles_filter = options.get(
            "singles_filter", CONFIG.download.singles_filter
        )

        async def safe_download_album(album: Album):
            await self._download_album(
                api, downloader, album, resource, options
            )

        futures = []

        if videos_filter != "none":
            offset = 0
            while True:
                artist_videos = await asyncio.to_thread(
                    api.get_artist_videos, resource.id, offset=offset
                )
                for video in artist_videos.items:
                    template = (
                        options.get("template") or CONFIG.templates.video
                    )
                    if "{album" in template and video.album:
                        album = await asyncio.to_thread(
                            api.get_album, video.album.id
                        )
                    else:
                        album = None
                    file_path_str = format_template(
                        template=template,
                        item=video,
                        album=album,
                        quality=self._get_item_quality(video, options),
                    )
                    futures.append(
                        downloader.download(
                            video, Path(file_path_str)
                        )
                    )
                offset += artist_videos.limit
                if offset > artist_videos.totalNumberOfItems:
                    break

        if videos_filter != "only":

            def get_all_albums(singles: bool):
                offset_a = 0
                while True:
                    artist_albums = api.get_artist_albums(
                        artist_id=resource.id,
                        offset=offset_a,
                        filter="EPSANDSINGLES" if singles else "ALBUMS",
                    )
                    for album_item in artist_albums.items:
                        futures.append(
                            safe_download_album(album_item)
                        )
                    offset_a += artist_albums.limit
                    if offset_a >= artist_albums.totalNumberOfItems:
                        break

            if singles_filter == "include":
                await asyncio.to_thread(get_all_albums, False)
                await asyncio.to_thread(get_all_albums, True)
            else:
                await asyncio.to_thread(
                    get_all_albums, singles_filter == "only"
                )

        if futures:
            await asyncio.gather(*futures)
