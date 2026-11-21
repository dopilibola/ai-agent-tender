"""Owner login for the public dashboard: one password from .env, an HMAC-signed cookie, CSRF tokens
and a small brute-force limiter. No user table — there is exactly one owner."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque

COOKIE = "fh_admin"
SESSION_TTL_S = 30 * 24 * 3600


class Auth:
    def __init__(self, password: str | None, pepper: str, user: str = "admin"):
        self.user = user
        self.password = password or ""
        # Changing the login/password (or the bot token) invalidates every existing cookie.
        self._key = hashlib.sha256(f"fh-web|{self.user}|{self.password}|{pepper}".encode()).digest()
        self._fails: dict[str, deque[float]] = {}
        self._global_fails: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def _sign(self, msg: str) -> str:
        return hmac.new(self._key, msg.encode(), hashlib.sha256).hexdigest()

    # ── session cookie ─────────────────────────────────────────────────────
    def issue(self, now: float | None = None) -> str:
        exp = int((now or time.time()) + SESSION_TTL_S)
        return f"{exp}.{self._sign(f'session|{exp}')}"

    def session_exp(self, cookie: str | None, now: float | None = None) -> int | None:
        """Expiry of a valid session cookie, else None."""
        if not self.enabled or not cookie or "." not in cookie:
            return None
        exp_s, sig = cookie.split(".", 1)
        if not exp_s.isdigit() or not hmac.compare_digest(sig, self._sign(f"session|{exp_s}")):
            return None
        exp = int(exp_s)
        return exp if exp > (now or time.time()) else None

    def csrf(self, exp: int) -> str:
        return self._sign(f"csrf|{exp}")[:32]

    def check_csrf(self, exp: int, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(self.csrf(exp), token or "")

    # ── login with rate limiting ───────────────────────────────────────────
    def _prune(self, q: deque[float], window: float, now: float) -> None:
        while q and q[0] < now - window:
            q.popleft()

    def locked(self, ip: str, now: float | None = None) -> bool:
        now = now or time.time()
        q = self._fails.setdefault(ip, deque())
        self._prune(q, 900, now)
        self._prune(self._global_fails, 3600, now)
        return len(q) >= 5 or len(self._global_fails) >= 30

    def try_login(self, ip: str, user: str, password: str, now: float | None = None) -> bool:
        now = now or time.time()
        if not self.enabled or self.locked(ip, now):
            return False
        user_ok = hmac.compare_digest(user.strip().lower().encode(), self.user.strip().lower().encode())
        if hmac.compare_digest(password.encode(), self.password.encode()) and user_ok:
            self._fails.pop(ip, None)
            return True
        self._fails.setdefault(ip, deque()).append(now)
        self._global_fails.append(now)
        if len(self._fails) > 10_000:  # don't grow forever under a spray
            self._fails.clear()
        return False
