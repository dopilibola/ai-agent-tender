"""📱 The phone drives its own Chrome: adb (Termux binary, callable from proot) + Chrome DevTools via `adb forward`.

The adb key of this phone's Termux is paired with the phone itself (Wireless debugging). The connect port changes
whenever Wireless debugging restarts, so it is re-discovered over mDNS — never by port scanning (a TCP probe
cancels an open pairing dialog and is pointless for the TLS connect port).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import shlex
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger(__name__)
CHROME = "com.android.chrome/com.google.android.apps.chrome.Main"
APP_ID = "fh-bid-bot"
PHONE_LOCK = asyncio.Lock()  # one Chrome tab: bids and Uzum price checks take turns
LOCAL = "127.0.0.1:5555"  # adbd's fixed port (`adb tcpip 5555`): reachable from Termux on any network


class PhoneUnavailable(RuntimeError):
    """adb / Wireless debugging / Chrome is not reachable — nothing was clicked."""


class CDPError(RuntimeError):
    pass


class Tab:
    """One Chrome tab over the DevTools websocket (only request/response; events are ignored)."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self.ws = ws
        self._id = 0

    async def call(self, method: str, timeout: float = 30, **params: Any) -> dict[str, Any]:
        self._id += 1
        mid = self._id
        await self.ws.send_json({"id": mid, "method": method, "params": params})
        async with asyncio.timeout(timeout):
            while True:
                msg = await self.ws.receive()
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                    raise CDPError("DevTools ulanishi uzildi")
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = msg.json()
                if data.get("id") == mid:
                    if "error" in data:
                        raise CDPError(str(data["error"].get("message")))
                    return data.get("result", {})

    async def eval(self, expression: str) -> Any:
        r = await self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise CDPError(str(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    async def screenshot(self, fmt: str = "jpeg", quality: int = 45) -> bytes:
        params: dict[str, Any] = {"format": fmt}
        if fmt == "jpeg":
            params["quality"] = quality
        r = await self.call("Page.captureScreenshot", **params)
        return base64.b64decode(r["data"])

    async def insert_text(self, text: str) -> None:
        await self.call("Input.insertText", text=text)

    async def wait_loaded(self, timeout: float = 30) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            with contextlib.suppress(CDPError):
                if await self.eval("document.readyState") == "complete":
                    return
            await asyncio.sleep(0.5)


class Phone:
    def __init__(self, adb_path: str, cdp_port: int = 9222, mdns_cmd: list[str] | None = None):
        self.adb_path = adb_path
        self.port = cdp_port
        self.mdns_cmd = mdns_cmd or []
        self.serial: str | None = None

    async def _run(self, *args: str, timeout: float = 30) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            raise PhoneUnavailable(f"{args[0]} ishga tushmadi: {e}") from e
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as e:
            proc.kill()
            raise PhoneUnavailable(f"{' '.join(args[1:3])}: vaqt tugadi") from e
        return out.decode(errors="replace")

    async def adb(self, *args: str, timeout: float = 30) -> str:
        target = ["-s", self.serial] if self.serial and args[0] not in ("devices", "connect", "disconnect") else []
        return await self._run(self.adb_path, *target, *args, timeout=timeout)

    async def _devices(self) -> list[str]:
        return re.findall(r"^(\S+)\tdevice\s*$", await self.adb("devices"), re.M)

    async def _pick(self) -> None:
        devs = await self._devices()
        self.serial = LOCAL if LOCAL in devs else (devs[0] if devs else None)

    async def _connect_local(self) -> None:
        await self.adb("connect", LOCAL, timeout=15)
        await asyncio.sleep(1)
        await self._pick()

    async def _connect(self) -> None:
        """Prefer adbd's fixed local port (works on any Wi-Fi / mobile data); fall back to Wireless debugging.

        `adb tcpip 5555` is lost on reboot. Once the owner re-enables Wireless debugging we get in over Wi-Fi
        (port found via mDNS) and switch adbd back to the fixed port ourselves.
        """
        await self._pick()
        if self.serial == LOCAL:
            return
        await self._connect_local()
        if self.serial == LOCAL:
            return
        if self.serial is None:
            if not self.mdns_cmd:
                raise PhoneUnavailable("adb ulanmagan")
            found = re.search(r"^connect (\S+) (\d+)", await self._run(*self.mdns_cmd, timeout=40), re.M)
            if not found:
                raise PhoneUnavailable("Simsiz disk raskadrovka o'chiq (telefon qayta yoqilgan yoki Wi-Fi yo'q)")
            out = await self.adb("connect", f"{found[1]}:{found[2]}", timeout=20)
            await asyncio.sleep(1)
            await self._pick()
            if self.serial is None:
                raise PhoneUnavailable(f"adb connect: {out.strip()[:150]}")
        await self.adb("tcpip", LOCAL.rsplit(":", 1)[1], timeout=20)  # restarts adbd on the fixed port
        await asyncio.sleep(4)
        await self.adb("disconnect", self.serial, timeout=10)
        self.serial = None
        await self._connect_local()
        if self.serial is None:
            raise PhoneUnavailable("adb doimiy portga o'tmadi")

    async def _cdp_alive(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s, s.get(f"http://127.0.0.1:{self.port}/json/version",
                                                           timeout=aiohttp.ClientTimeout(total=4)) as r:
                return r.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    async def ensure(self) -> None:
        """adb connected, screen on, Chrome running, DevTools forwarded — or PhoneUnavailable."""
        await self._connect()
        await self.adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        await self.adb("shell", "wm", "dismiss-keyguard")
        await self.adb("forward", f"tcp:{self.port}", "localabstract:chrome_devtools_remote")
        if await self._cdp_alive():
            return
        await self.adb("shell", "am", "start", "-n", CHROME)
        for _ in range(15):
            await asyncio.sleep(1)
            await self.adb("forward", f"tcp:{self.port}", "localabstract:chrome_devtools_remote")
            if await self._cdp_alive():
                return
        raise PhoneUnavailable("Chrome DevTools javob bermadi")

    async def _targets(self, http: aiohttp.ClientSession) -> list[dict[str, Any]]:
        async with http.get(f"http://127.0.0.1:{self.port}/json/list") as r:
            return [t for t in await r.json(content_type=None) if t.get("type") == "page"]

    @contextlib.asynccontextmanager
    async def open(self, url: str) -> AsyncIterator[Tab]:
        """Show `url` on the phone's screen in the bot's own Chrome tab and attach DevTools to it.

        Android Chrome refuses DevTools /json/new, so the page is opened with a VIEW intent. The fixed
        application_id makes Chrome reuse one tab for every bid instead of piling up tabs (RAM is scarce).
        """
        async with aiohttp.ClientSession() as http:
            before = {t["id"] for t in await self._targets(http)}
            await self.adb("shell", f"am start -a android.intent.action.VIEW -d {shlex.quote(url)} "
                                    f"-e com.android.browser.application_id {APP_ID} -p com.android.chrome")
            key = _url_key(url)
            target = None
            for _ in range(30):
                await asyncio.sleep(0.5)
                pages = await self._targets(http)
                target = (next((t for t in pages if t["id"] not in before), None)
                          or next((t for t in pages if _url_key(t.get("url", "")) == key), None))
                if target:
                    break
            if target is None:
                raise PhoneUnavailable("Chrome'da sahifa ochilmadi")
            async with http.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=64 * 1024 * 1024) as ws:
                tab = Tab(ws)
                await tab.wait_loaded()
                yield tab


def _url_key(url: str) -> str:
    p = urlsplit(url)
    return f"{p.netloc.removeprefix('www.').removeprefix('m.')}{p.path.rstrip('/')}"
