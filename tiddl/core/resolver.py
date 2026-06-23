import asyncio
from logging import getLogger
from pathlib import Path
from typing import Any, AsyncIterator

from tiddl.cli.utils.resource import TidalResource
from tiddl.core.api import TidalAPI
from tiddl.core.api.models import Album, Track, Video
from tiddl.core.utils.format import format_template

log = getLogger(__name__)


class ResourceResolver:
    """Shared logic for resolving TidalResource to downloadable items.

    Both CLI and GUI use this to avoid duplicating the resource-to-items
    resolution logic.
    """

    def __init__(self, api: TidalAPI):
        self.api = api

    @staticmethod
    def _get_item_quality(item: Track | Video, options: dict) -> str:
        """Return the quality string to use for format_template."""
        track_q: str = options.get("track_quality", "high")
        video_q: str = options.get("video_quality", "fhd")
        if isinstance(item, Track):
            if track_q in ("low", "normal"):
                return track_q.upper()
            if track_q == "max" and "HIRES_LOSSLESS" not in item.mediaMetadata.tags:
                return "HIGH"
            return track_q.upper()
        elif isinstance(item, Video):
            return video_q.upper()
        return track_q.upper()

    async def resolve(
        self, resource: TidalResource, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        """Yield (item, file_path) pairs for download.

        Handles all resource types: track, video, album, playlist, artist, mix.

        ``options`` may contain:
        - template      — file path template (caller resolves config default)
        - track_quality — one of "low", "normal", "high", "max"
        - video_quality — one of "sd", "hd", "fhd"
        - videos_filter — "none", "allow", "only"
        - singles_filter — "none", "only", "include"
        """
        match resource.type:
            case "track":
                async for tup in self._resolve_track(resource, options):
                    yield tup

            case "video":
                async for tup in self._resolve_video(resource, options):
                    yield tup

            case "album":
                album = await asyncio.to_thread(self.api.get_album, int(resource.id))
                template = options.get("template", "")
                async for tup in self._resolve_album_items(album, template, options):
                    yield tup

            case "playlist":
                async for tup in self._resolve_playlist(resource, options):
                    yield tup

            case "artist":
                async for tup in self._resolve_artist(resource, options):
                    yield tup

            case "mix":
                async for tup in self._resolve_mix(resource, options):
                    yield tup

    # ------------------------------------------------------------------
    # Internal per-type resolvers
    # ------------------------------------------------------------------

    async def _resolve_track(
        self, resource: TidalResource, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        track = await asyncio.to_thread(self.api.get_track, int(resource.id))
        album = await asyncio.to_thread(self.api.get_album, track.album.id)
        file_path = format_template(
            template=options.get("template", ""),
            item=track,
            album=album,
            quality=self._get_item_quality(track, options),
        )
        yield track, Path(file_path)

    async def _resolve_video(
        self, resource: TidalResource, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        video = await asyncio.to_thread(self.api.get_video, int(resource.id))
        template = options.get("template", "")
        if "{album" in template and video.album and video.album.id is not None:
            album = await asyncio.to_thread(self.api.get_album, video.album.id)
        else:
            album = None
        file_path = format_template(
            template=template,
            item=video,
            album=album,
            quality=self._get_item_quality(video, options),
        )
        yield video, Path(file_path)

    async def _resolve_album_items(
        self, album: Album, template: str, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        offset = 0
        while True:
            album_items = await asyncio.to_thread(
                self.api.get_album_items_credits, album.id, offset=offset
            )
            for album_item in album_items.items:
                file_path = format_template(
                    template=template,
                    item=album_item.item,
                    album=album,
                    quality=self._get_item_quality(album_item.item, options),
                )
                yield album_item.item, Path(file_path)
            offset += album_items.limit
            if offset >= album_items.totalNumberOfItems:
                break

    async def _resolve_playlist(
        self, resource: TidalResource, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        playlist = await asyncio.to_thread(self.api.get_playlist, resource.id)
        template = options.get("template", "")
        offset = 0
        playlist_index = 0
        while True:
            playlist_items = await asyncio.to_thread(
                self.api.get_playlist_items, resource.id, offset=offset
            )
            for pl_item in playlist_items.items:
                playlist_index += 1
                if "{album" in template:
                    album = await asyncio.to_thread(
                        self.api.get_album, pl_item.item.album.id
                    )
                else:
                    album = None
                file_path = format_template(
                    template=template,
                    item=pl_item.item,
                    album=album,
                    playlist=playlist,
                    playlist_index=playlist_index,
                    quality=self._get_item_quality(pl_item.item, options),
                )
                yield pl_item.item, Path(file_path)
            offset += playlist_items.limit
            if offset >= playlist_items.totalNumberOfItems:
                break

    async def _resolve_artist(
        self, resource: TidalResource, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        videos_filter: str = options.get("videos_filter", "none")
        singles_filter: str = options.get("singles_filter", "none")
        template = options.get("template", "")

        all_items: list[tuple[Track | Video, Path]] = []

        if videos_filter != "none":
            offset = 0
            while True:
                artist_videos = await asyncio.to_thread(
                    self.api.get_artist_videos, int(resource.id), offset=offset
                )
                for video in artist_videos.items:
                    video_template = template
                    if "{album" in video_template and video.album:
                        album = await asyncio.to_thread(
                            self.api.get_album, video.album.id
                        )
                    else:
                        album = None
                    file_path = format_template(
                        template=video_template,
                        item=video,
                        album=album,
                        quality=self._get_item_quality(video, options),
                    )
                    all_items.append((video, Path(file_path)))
                offset += artist_videos.limit
                if offset > artist_videos.totalNumberOfItems:
                    break

        if videos_filter != "only":

            def get_artist_albums(singles: bool):
                offset_a = 0
                while True:
                    artist_albums = self.api.get_artist_albums(
                        artist_id=int(resource.id),
                        offset=offset_a,
                        filter="EPSANDSINGLES" if singles else "ALBUMS",
                    )
                    for album_entry in artist_albums.items:
                        yield album_entry
                    offset_a += artist_albums.limit
                    if offset_a >= artist_albums.totalNumberOfItems:
                        break

            if singles_filter == "include":
                for album_entry in get_artist_albums(False):
                    async for tup in self._resolve_album_items(
                        album_entry, template, options
                    ):
                        all_items.append(tup)
                for album_entry in get_artist_albums(True):
                    async for tup in self._resolve_album_items(
                        album_entry, template, options
                    ):
                        all_items.append(tup)
            elif singles_filter == "only":
                for album_entry in get_artist_albums(True):
                    async for tup in self._resolve_album_items(
                        album_entry, template, options
                    ):
                        all_items.append(tup)
            else:
                for album_entry in get_artist_albums(False):
                    async for tup in self._resolve_album_items(
                        album_entry, template, options
                    ):
                        all_items.append(tup)

        for item, path in all_items:
            yield item, path

    async def _resolve_mix(
        self, resource: TidalResource, options: dict
    ) -> AsyncIterator[tuple[Track | Video, Path]]:
        template = options.get("template", "")
        offset = 0
        while True:
            mix_items = await asyncio.to_thread(
                self.api.get_mix_items, resource.id, offset=0
            )
            for mix_item in mix_items.items:
                if "{album" in template:
                    album = await asyncio.to_thread(
                        self.api.get_album, mix_item.item.album.id
                    )
                else:
                    album = None
                file_path = format_template(
                    template=template,
                    item=mix_item.item,
                    album=album,
                    mix_id=resource.id,
                    quality=self._get_item_quality(mix_item.item, options),
                )
                yield mix_item.item, Path(file_path)
            offset += mix_items.limit
            if offset >= mix_items.totalNumberOfItems:
                break
