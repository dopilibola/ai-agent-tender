"""Read public listing pages in the phone's own Chrome (A23), the same pages a person sees.

Used for sites whose list cannot be read over plain HTTP: xarid.uzex.uz auctions (every API call needs a
token that the site's own JavaScript makes) and xt-xarid.uz / Hayot birja (robots.txt bars /api/, the
/procedure/ pages are allowed). Nothing is clicked except "next page" / "show more" on a public list.

The phone has one Chrome tab for the bot, shared with the bid submitter and the Uzum price check, so every
visit holds PHONE_LOCK. Lot cards are cached on disk (data/phone_cards/<source>/<id>.json) so the tender
scan, the goods page and the price check open each card at most once in a while.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class PhoneBrowser:
    def __init__(self, phone, *, wait_s: float = 25, delay_s: float = 3):
        self.phone = phone
        self.wait_s = wait_s
        self.delay_s = delay_s
        self._ensured_at = 0.0

    @contextlib.asynccontextmanager
    async def page(self, url: str) -> AsyncIterator[Any]:
        """Open `url` in the bot's tab while holding the phone; yields the DevTools tab."""
        from app.phone.device import PHONE_LOCK

        async with PHONE_LOCK:
            if time.monotonic() - self._ensured_at > 120:
                await self.phone.ensure()
                self._ensured_at = time.monotonic()
            try:
                async with self.phone.open(url) as tab:
                    yield tab
            finally:
                await asyncio.sleep(self.delay_s)  # pause before the next page on the same site

    async def wait_for(self, tab, js: str, *, limit_s: float | None = None) -> Any:
        """Evaluate `js` until it returns something truthy (the SPA renders after load) or time runs out."""
        from app.phone.device import CDPError

        loop = asyncio.get_running_loop()
        end = loop.time() + (limit_s or self.wait_s)
        value: Any = None
        while loop.time() < end:
            try:
                value = await tab.eval(js)
            except CDPError:  # navigation in progress
                value = None
            if value:
                return value
            await asyncio.sleep(1)
        return value

    async def read(self, url: str, ready_js: str, js: str, *, limit_s: float | None = None) -> Any:
        async with self.page(url) as tab:
            await self.wait_for(tab, ready_js, limit_s=limit_s)
            await asyncio.sleep(1)  # let the rest of the first screen render
            return await tab.eval(js)


class CardCache:
    """Parsed lot cards on disk, one JSON file per lot, kept for `ttl_s`."""

    def __init__(self, root: Path, ttl_s: float = 6 * 3600):
        self.root = root
        self.ttl_s = ttl_s

    def _path(self, lot_id: str) -> Path:
        return self.root / f"{lot_id}.json"

    def get(self, lot_id: str) -> dict | None:
        p = self._path(lot_id)
        try:
            if time.time() - p.stat().st_mtime > self.ttl_s:
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, lot_id: str, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(lot_id).with_suffix(".part")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(lot_id))


class JsonStore:
    """A small dict kept in one JSON file (names and deadlines remembered between scans)."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def save(self, keep: set[str] | None = None) -> None:
        if keep is not None:
            self.data = {k: v for k, v in self.data.items() if k in keep}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".part")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
