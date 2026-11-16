"""Bridges the engine and bot.py. Builds the state dict bot.py expects, runs it in a
worker process with a hard timeout, and validates whatever comes back.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import threading
import time
from pathlib import Path

from . import bot_worker
from .engine import Hand

log = logging.getLogger("poker.bot")


def _bot_from_secret() -> str | None:
    """Materialise the bot from the BOT_SOURCE_B64 secret (gzip + base64 of bot.py).

    For hosts that build from git (e.g. Fly's GitHub launcher), where bot.py can't be in the
    build. The file goes to a fresh owner-only temp dir and the secret is removed from this
    process's environment so nothing spawned later inherits it.
    """
    enc = os.environ.pop("BOT_SOURCE_B64", None)
    if not enc:
        return None
    import base64
    import gzip
    import tempfile
    src = gzip.decompress(base64.b64decode("".join(enc.split())))
    d = tempfile.mkdtemp(prefix="bot-")            # created 0700
    path = os.path.join(d, "bot.py")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(src)
    return path


def find_bot_path() -> str:
    secret_path = _bot_from_secret()
    if secret_path:
        return secret_path
    candidates = [os.environ.get("BOT_PATH"), "/etc/secrets/bot.py",
                  str(Path(__file__).resolve().parent.parent / "bot.py")]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise RuntimeError("bot.py not found: set the BOT_SOURCE_B64 secret, set BOT_PATH, "
                       "or place bot.py in the project root")


# ------------------------------------------------------------------ state translation

def bot_state(hand: Hand, seat: int) -> dict:
    """The decision state in bot.py's format. Contains only what the bot's seat may see."""
    legal = hand.legal(seat)
    names = [a for a in ("fold", "check", "call", "raise") if a in legal]
    r = legal.get("raise")
    return {
        "street": hand.street,
        "seat": seat,
        "button": hand.button,
        "hole_cards": list(hand.hole[seat]),
        "board": list(hand.board),
        "stacks": list(hand.stacks),
        "bets": list(hand.bets),
        "folded": list(hand.folded),
        "pot": hand.pot,
        "to_call": hand.to_call(seat),
        "big_blind": hand.big_blind,
        "legal_actions": names,
        "min_raise_to": r["min"] if r else None,
        "max_raise_to": r["max"] if r else None,
        "raises_this_street": hand.raises_this_street,
        "history": [dict(h) for h in hand.history],
    }


def hand_summary(hand: Hand) -> dict:
    """End-of-hand summary for on_hand_end. Opponent cards only if shown at showdown."""
    shown = {}
    if hand.result and hand.result.showdown:
        shown = {i: list(hand.hole[i]) for i in (0, 1) if not hand.folded[i]}
    return {
        "history": [dict(h) for h in hand.history],
        "shown": shown,
        "board": list(hand.board),
        "stacks": list(hand.stacks),
    }


def sanitize(hand: Hand, seat: int, action: dict | None) -> tuple[str, int | None]:
    """Turn the bot's reply into a definitely-legal engine action."""
    legal = hand.legal(seat)
    fallback = ("check", None) if "check" in legal else ("fold", None)
    if not action:
        return fallback
    a = action.get("action")
    if a == "raise":
        r = legal.get("raise")
        if not r:
            return ("call", None) if "call" in legal else fallback
        amt = action.get("amount")
        if not isinstance(amt, int):
            return fallback
        return "raise", max(r["min"], min(r["max"], amt))
    if a == "call":
        return ("call", None) if "call" in legal else fallback
    if a == "check":
        return fallback
    if a == "fold":
        return fallback                        # never fold when checking is free
    return fallback


# ------------------------------------------------------------------ worker pool

class _Worker:
    def __init__(self, bot_path: str):
        self.bot_path = bot_path
        self.lock = threading.Lock()
        self.req = 0
        self.generation = 0
        self._spawn()

    def _spawn(self):
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=bot_worker.serve, args=(child, self.bot_path), daemon=True)
        self.proc.start()
        child.close()
        self.conn = parent
        if not parent.poll(30) or parent.recv()[1] is not True:
            raise RuntimeError("bot worker failed to start")
        self.generation += 1

    def restart(self):
        try:
            self.proc.kill()
            self.proc.join(2)
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass
        self._spawn()

    def call(self, op: str, match_id: str, payload, timeout: float):
        """Blocking call. Returns (ok, value, generation_before)."""
        with self.lock:
            gen = self.generation
            self.req += 1
            rid = self.req
            try:
                self.conn.send((rid, op, match_id, payload))
                deadline = time.monotonic() + timeout
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0 or not self.conn.poll(left):
                        log.warning("bot %s timed out after %.1fs; restarting worker", op, timeout)
                        self.restart()
                        return False, None, gen
                    got_id, ok, value = self.conn.recv()
                    if got_id == rid:
                        return ok, value, gen
            except (EOFError, OSError, BrokenPipeError):
                log.warning("bot worker died during %s; restarting", op)
                self.restart()
                return False, None, gen


class BotPool:
    """A small pool of worker processes; each match is pinned to one worker."""

    def __init__(self, size: int | None = None, timeout: float | None = None, bot_path: str | None = None):
        self.bot_path = bot_path or find_bot_path()
        self.size = size or int(os.environ.get("BOT_WORKERS", "2"))
        self.timeout = timeout or float(os.environ.get("BOT_TIMEOUT", "3.0"))
        self.workers = [_Worker(self.bot_path) for _ in range(self.size)]
        self._assign: dict[str, int] = {}
        self._started: dict[str, tuple[int, dict]] = {}   # match -> (worker generation, table info)
        self._next = 0

    def _worker(self, match_id: str) -> _Worker:
        if match_id not in self._assign:
            self._assign[match_id] = self._next % self.size
            self._next += 1
        return self.workers[self._assign[match_id]]

    def _call_sync(self, op, match_id, payload):
        w = self._worker(match_id)
        if op != "start" and match_id in self._started and self._started[match_id][0] != w.generation:
            # The worker was restarted (timeout elsewhere); the agent's memory is gone, so
            # seat a fresh one for this match before continuing.
            log.warning("re-seating bot for match after worker restart")
            info = self._started[match_id][1]
            ok, _, _ = w.call("start", match_id, info, self.timeout)
            self._started[match_id] = (w.generation, info)
        ok, value, _ = w.call(op, match_id, payload, self.timeout)
        if op == "start":
            self._started[match_id] = (w.generation, payload)
        return ok, value

    async def _call(self, op, match_id, payload):
        return await asyncio.to_thread(self._call_sync, op, match_id, payload)

    async def start(self, match_id: str, info: dict) -> None:
        await self._call("start", match_id, info)

    async def act(self, match_id: str, hand: Hand, seat: int) -> tuple[str, int | None]:
        ok, value = await self._call("act", match_id, bot_state(hand, seat))
        return sanitize(hand, seat, value if ok else None)

    async def hand_end(self, match_id: str, hand: Hand) -> None:
        await self._call("end", match_id, hand_summary(hand))

    async def drop(self, match_id: str) -> None:
        if match_id in self._assign:
            await self._call("drop", match_id, None)
            self._assign.pop(match_id, None)
            self._started.pop(match_id, None)

    def close(self):
        for w in self.workers:
            try:
                w.proc.kill()
            except Exception:
                pass
