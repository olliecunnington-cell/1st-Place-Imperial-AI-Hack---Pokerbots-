"""Session tokens, username validation and rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

# ------------------------------------------------------------------ signed session tokens


class Signer:
    def __init__(self, key: str):
        self.key = key.encode()

    def _sig(self, payload: str) -> str:
        mac = hmac.new(self.key, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    def issue(self) -> tuple[str, str]:
        """Returns (session_id, token)."""
        sid = secrets.token_hex(16)
        payload = f"{sid}.{int(time.time())}"
        return sid, f"{payload}.{self._sig(payload)}"

    def verify(self, token: str | None, max_age: float) -> str | None:
        """Returns the session id if the token is genuine and fresh."""
        if not token or len(token) > 200:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        sid, ts, sig = parts
        if not re.fullmatch(r"[0-9a-f]{32}", sid) or not ts.isdigit():
            return None
        if not hmac.compare_digest(sig, self._sig(f"{sid}.{ts}")):
            return None
        if time.time() - int(ts) > max_age:
            return None
        return sid


# ------------------------------------------------------------------ usernames

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")

# Matched as substrings after normalising case, digits-as-letters and underscores.
_BLOCKED = (
    "fuck", "fuk", "shit", "cunt", "bitch", "nigg", "faggot", "retard", "whore", "slut",
    "pussy", "penis", "vagina", "twat", "wank", "bastard", "asshole", "arsehole", "nazi",
    "hitler", "kike", "chink", "wetback", "tranny", "dyke", "porn", "molest", "paedo", "pedophile",
    "jizz", "kkk", "dildo", "blowjob", "handjob", "rapes", "raping",
)
# Only blocked as the whole name: they occur inside innocent words
# (therapist, grape, peacock, document, analyst, spicy, torpedo, crisis).
_BLOCKED_EXACT = ("ass", "tit", "tits", "hoe", "sex", "kys", "rape", "rapist", "cum",
                  "anal", "spic", "pedo", "isis", "cock", "dick", "fag", "fags", "boob", "boobs")
# Names players might use to impersonate the house.
_RESERVED = ("bot", "thebot", "admin", "administrator", "moderator", "mod", "dealer", "house",
             "system", "server", "root", "support", "official", "staff")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "$": "s", "@": "a"})


def validate_username(name) -> str | None:
    """Returns an error message, or None if the name is acceptable."""
    if not isinstance(name, str) or not USERNAME_RE.fullmatch(name):
        return "Use 3 to 16 letters, numbers or underscores."
    low = name.lower().replace("_", "")
    leet = low.translate(_LEET)
    letters = re.sub(r"[0-9]", "", low)
    variants = {leet, letters, re.sub(r"(.)\1+", r"\1", leet), re.sub(r"(.)\1+", r"\1", letters)}
    for variant in variants:
        if any(w in variant for w in _BLOCKED) or variant in _BLOCKED_EXACT:
            return "Please choose a different name."
        if variant in _RESERVED:
            return "That name is reserved."
    return None


# ------------------------------------------------------------------ rate limiting

class RateLimiter:
    """Sliding-window limiter keyed by arbitrary strings (IP, session id, ...)."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def sweep(self):
        now = time.monotonic()
        for k in [k for k, q in self.hits.items() if not q or now - q[-1] > self.window]:
            del self.hits[k]
