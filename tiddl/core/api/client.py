import asyncio
import json
from logging import getLogger
from pathlib import Path
from typing import Any, Type, TypeVar, Callable, Optional

from aiohttp import ClientError
from aiohttp_client_cache import CachedSession, SQLiteBackend
from aiohttp_client_cache.cache_control import DO_NOT_CACHE
from pydantic import BaseModel

from .exceptions import ApiError

T = TypeVar("T", bound=BaseModel)

API_URL = "https://api.tidal.com/v1"
MAX_RETRIES = 5
RETRY_DELAY = 2

log = getLogger(__name__)


class TidalClient:
    _token: str
    debug_path: Path | None
    on_token_expiry: Optional[Callable[[], str | None]]
    _is_refreshing: bool
    _session: CachedSession | None
    _cache_name: str
    _omit_cache: bool

    def __init__(
        self,
        token: str,
        cache_name: str,
        omit_cache: bool = False,
        debug_path: Path | None = None,
        on_token_expiry: Optional[Callable[[], str | None]] = None,
    ) -> None:
        self.on_token_expiry = on_token_expiry
        self.debug_path = debug_path
        self._token = token
        self._cache_name = cache_name
        self._omit_cache = omit_cache
        self._is_refreshing = False
        self._session = None

    async def _get_session(self) -> CachedSession:
        if self._session is None:
            cache_backend = SQLiteBackend(
                cache_name=self._cache_name,
                allowed_methods=("GET",),
            )
            self._session = CachedSession(cache=cache_backend)
        return self._session

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, token: str):
        self._token = token

    async def fetch(
        self,
        model: Type[T],
        endpoint: str,
        params: dict[str, Any] = {},
        expire_after: int = -1,
        _attempt: int = 1,
    ) -> T:
        """
        Fetch data from the API endpoint
        and parse it into the given Pydantic model.
        """
        session = await self._get_session()

        if self._omit_cache:
            effective_expire = DO_NOT_CACHE
        else:
            effective_expire = expire_after

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

        try:
            async with session.get(
                f"{API_URL}/{endpoint}",
                params=params,
                headers=headers,
                expire_after=effective_expire,
            ) as res:
                status_code = res.status
                from_cache = res.from_cache

                if status_code == 429 and _attempt < MAX_RETRIES:
                    retry_after = int(
                        res.headers.get("Retry-After", str(RETRY_DELAY))
                    )
                    log.warning(
                        f"Rate limited (429), retrying {_attempt}/{MAX_RETRIES} after {retry_after}s"
                    )
                    await asyncio.sleep(retry_after)
                    return await self.fetch(
                        model=model,
                        endpoint=endpoint,
                        params=params,
                        expire_after=expire_after,
                        _attempt=_attempt + 1,
                    )

                if status_code >= 500 and _attempt < MAX_RETRIES:
                    log.warning(
                        f"Server error {status_code}, retrying {_attempt}/{MAX_RETRIES}"
                    )
                    await asyncio.sleep(RETRY_DELAY * _attempt)
                    return await self.fetch(
                        model=model,
                        endpoint=endpoint,
                        params=params,
                        expire_after=expire_after,
                        _attempt=_attempt + 1,
                    )

                if (
                    status_code == 401
                    and self.on_token_expiry
                    and not self._is_refreshing
                ):
                    self._is_refreshing = True
                    try:
                        token = self.on_token_expiry()
                        if token:
                            self.token = token
                            return await self.fetch(
                                model=model,
                                endpoint=endpoint,
                                params=params,
                                expire_after=expire_after,
                                _attempt=MAX_RETRIES - 1,
                            )
                    finally:
                        self._is_refreshing = False

                log.debug(
                    f"{endpoint} {params} '{'HIT' if from_cache else 'MISS'}' [{status_code}]",
                )

                try:
                    data = await res.json()
                except Exception as e:
                    if _attempt >= MAX_RETRIES:
                        log.error(
                            f"JSON decode failed after {MAX_RETRIES} attempts: {e}"
                        )
                        raise ApiError(
                            status=status_code,
                            subStatus="0",
                            userMessage="Response body does not contain valid json.",
                        )

                    log.warning(
                        f"JSON decode error, retrying {_attempt}/{MAX_RETRIES}"
                    )
                    await asyncio.sleep(RETRY_DELAY)

                    return await self.fetch(
                        model=model,
                        endpoint=endpoint,
                        params=params,
                        expire_after=expire_after,
                        _attempt=_attempt + 1,
                    )

                if self.debug_path:
                    file = self.debug_path / f"{endpoint}.json"
                    file.parent.mkdir(parents=True, exist_ok=True)

                    file.write_text(
                        json.dumps(
                            {
                                "status_code": status_code,
                                "endpoint": endpoint,
                                "params": params,
                                "data": data,
                            },
                            indent=2,
                        )
                    )

                if status_code != 200:
                    log.error(f"{endpoint=}, {params=}, {data=}")
                    raise ApiError(**data)

                return model.model_validate(data)

        except (ClientError, asyncio.TimeoutError) as e:
            if _attempt >= MAX_RETRIES:
                raise ApiError(
                    status=0,
                    subStatus="0",
                    userMessage=f"Connection failed after {MAX_RETRIES} attempts",
                )
            log.warning(
                f"Connection error, retrying {_attempt}/{MAX_RETRIES}: {e}"
            )
            await asyncio.sleep(RETRY_DELAY * _attempt)
            return await self.fetch(
                model=model,
                endpoint=endpoint,
                params=params,
                expire_after=expire_after,
                _attempt=_attempt + 1,
            )

    def fetch_sync(
        self,
        model: Type[T],
        endpoint: str,
        params: dict[str, Any] = {},
        expire_after: int = -1,
        _attempt: int = 1,
    ) -> T:
        """Sync wrapper around fetch() for CLI backward compat."""
        return asyncio.run(
            self.fetch(
                model=model,
                endpoint=endpoint,
                params=params,
                expire_after=expire_after,
                _attempt=_attempt,
            )
        )
