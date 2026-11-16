"""Polite HTTP client: one request at a time, >= N seconds apart, plain browser UA, retries on 5xx."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class FileTooLarge(Exception):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError)


class PoliteClient:
    def __init__(
        self,
        *,
        delay_s: float = 2.5,
        headers: dict[str, str] | None = None,
        timeout_s: float = 40,
        transport: httpx.AsyncBaseTransport | None = None,
        attempts: int = 3,
    ) -> None:
        self.delay_s = delay_s
        self.attempts = attempts
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*", **(headers or {})},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def _pace(self) -> None:
        wait = self._last + self.delay_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_retryable),
            wait=wait_exponential_jitter(initial=self.delay_s, max=30),
            stop=stop_after_attempt(self.attempts),
            reraise=True,
        ):
            with attempt:
                async with self._lock:
                    await self._pace()
                    resp = await self._client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
        raise AssertionError("unreachable")  # pragma: no cover

    async def get_json(self, url: str, **kwargs):
        return (await self.request("GET", url, **kwargs)).json()

    async def post_json(self, url: str, payload, **kwargs):
        return (await self.request("POST", url, json=payload, **kwargs)).json()

    async def download(self, method: str, url: str, dest: Path, *, max_bytes: int, **kwargs) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        async with self._lock:
            await self._pace()
            async with self._client.stream(method, url, **kwargs) as resp:
                resp.raise_for_status()
                size = 0
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            fh.close()
                            tmp.unlink(missing_ok=True)
                            raise FileTooLarge(f"{url} > {max_bytes} bytes")
                        fh.write(chunk)
        tmp.replace(dest)
        return size

    async def aclose(self) -> None:
        await self._client.aclose()
