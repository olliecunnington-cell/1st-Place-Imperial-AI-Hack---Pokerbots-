"""Match lifecycle: dealing hands, human and bot turns, scoring, expiry.

The client only ever sees what `view()` builds. That is the single place deciding what
leaves the server, and it never includes the deck or the bot's cards before showdown.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import time

from . import db
from .bot_adapter import BotPool
from .engine import Hand, IllegalAction, hand_name

log = logging.getLogger("poker.match")

HUMAN, BOT = 0, 1
SMALL_BLIND = 10
BIG_BLIND = 20
STARTING_STACK = 2000                     # 100 big blinds, reset every hand (bot.py's format)


def _env_float(name, default):
    return float(os.environ.get(name, default))


class Match:
    def __init__(self, session_id: str, username: str, hands_total: int):
        self.id = secrets.token_urlsafe(12)
        self.session_id = session_id
        self.username = username
        self.hands_total = hands_total
        self.hand_no = 0
        self.net = 0
        self.hand: Hand | None = None
        self.status = "playing"           # playing | finished | abandoned
        self.bot_thinking = False
        self.log: list[dict] = []
        self.seq = 0
        self.last_activity = time.monotonic()
        self.finished_at: float | None = None
        self.lock = asyncio.Lock()
        self.sockets: set = set()
        self.bot_task: asyncio.Task | None = None

    @property
    def hand_over(self) -> bool:
        return self.hand is None or self.hand.finished

    def touch(self):
        self.last_activity = time.monotonic()


class MatchManager:
    def __init__(self, pool: BotPool, hands_total: int | None = None):
        self.pool = pool
        self.hands_total = hands_total or int(os.environ.get("MATCH_HANDS", "50"))
        self.idle_timeout = _env_float("MATCH_IDLE_SECONDS", "600")
        self.delay_min = _env_float("BOT_DELAY_MIN", "0.7")
        self.delay_max = _env_float("BOT_DELAY_MAX", "1.4")
        self.max_active = int(os.environ.get("MAX_ACTIVE_MATCHES", "200"))
        self.matches: dict[str, Match] = {}
        self.by_session: dict[str, str] = {}

    # ------------------------------------------------------------ lookup / lifecycle
    def current(self, session_id: str) -> Match | None:
        mid = self.by_session.get(session_id)
        return self.matches.get(mid) if mid else None

    def active_count(self) -> int:
        return sum(1 for m in self.matches.values() if m.status == "playing")

    async def create(self, session_id: str, username: str) -> Match:
        old = self.current(session_id)
        if old and old.status == "playing":
            await self.abandon(old, "replaced by a new match")
        m = Match(session_id, username, self.hands_total)
        self.matches[m.id] = m
        self.by_session[session_id] = m.id
        db.create_match(m.id, username, session_id, m.hands_total, BIG_BLIND)
        await self.pool.start(m.id, {"seat": BOT, "num_players": 2, "hands": m.hands_total,
                                     "starting_stack": STARTING_STACK, "big_blind": BIG_BLIND})
        async with m.lock:
            self._deal(m)
        self._kick_bot(m)
        return m

    async def abandon(self, m: Match, reason: str):
        async with m.lock:
            if m.status != "playing":
                return
            m.status = "abandoned"
            m.finished_at = time.monotonic()
            m.seq += 1
        db.finish_match(m.id, "abandoned", len(m.log), m.net, BIG_BLIND)
        if m.bot_task:
            m.bot_task.cancel()
        await self.pool.drop(m.id)
        await self.broadcast(m, {"type": "expired", "reason": reason})
        log.info("match abandoned: %s", reason)

    async def sweep(self):
        now = time.monotonic()
        for m in list(self.matches.values()):
            if m.status == "playing" and now - m.last_activity > self.idle_timeout:
                await self.abandon(m, "inactive for too long")
            elif m.status != "playing" and m.finished_at and now - m.finished_at > 900:
                self.matches.pop(m.id, None)
                if self.by_session.get(m.session_id) == m.id:
                    self.by_session.pop(m.session_id, None)

    # ------------------------------------------------------------ dealing
    def _deal(self, m: Match):
        m.hand_no += 1
        button = HUMAN if m.hand_no % 2 == 1 else BOT      # alternate the button
        m.hand = Hand(stacks=[STARTING_STACK, STARTING_STACK], button=button,
                      small_blind=SMALL_BLIND, big_blind=BIG_BLIND)
        m.seq += 1

    async def _end_hand(self, m: Match):
        h = m.hand
        r = h.result
        m.net += r.net[HUMAN]
        m.log.append(self._hand_record(m, h))
        await self.pool.hand_end(m.id, h)
        if m.hand_no >= m.hands_total:
            m.status = "finished"
            m.finished_at = time.monotonic()
            db.finish_match(m.id, "completed", len(m.log), m.net, BIG_BLIND)
            await self.pool.drop(m.id)

    # ------------------------------------------------------------ human input
    async def human_action(self, m: Match, msg: dict) -> str | None:
        """Apply a validated message from the player. Returns an error string or None."""
        async with m.lock:
            if m.status != "playing":
                return "This match is over."
            if msg.get("seq") != m.seq:
                return "Out of date: the table has moved on."
            kind = msg.get("type")
            if kind == "next":
                if not m.hand_over:
                    return "The hand is still in progress."
                m.touch()
                self._deal(m)
            elif kind == "action":
                h = m.hand
                if m.hand_over or m.bot_thinking or h.to_act != HUMAN:
                    return "It is not your turn."
                action = msg.get("action")
                amount = msg.get("amount")
                if action not in ("fold", "check", "call", "raise"):
                    return "Unknown action."
                if action == "raise":
                    if isinstance(amount, bool) or not isinstance(amount, int):
                        return "Raise amount must be a whole number."
                else:
                    amount = None
                try:
                    h.act(HUMAN, action, amount)
                except IllegalAction as e:
                    return str(e)
                m.touch()
                m.seq += 1
                if h.finished:
                    await self._end_hand(m)
            else:
                return "Unknown message."
        await self.broadcast_state(m)
        self._kick_bot(m)
        return None

    # ------------------------------------------------------------ bot turns
    def _kick_bot(self, m: Match):
        if m.status == "playing" and not m.hand_over and m.hand.to_act == BOT:
            if not m.bot_task or m.bot_task.done():
                m.bot_task = asyncio.create_task(self._bot_turns(m))

    async def _bot_turns(self, m: Match):
        try:
            while True:
                async with m.lock:
                    h = m.hand
                    if m.status != "playing" or h.finished or h.to_act != BOT:
                        m.bot_thinking = False
                        return
                    m.bot_thinking = True
                    m.seq += 1
                    stamp = (m.hand_no, len(h.history))
                await self.broadcast_state(m)
                # Pad every decision to a random delay so think time reveals nothing.
                target = random.uniform(self.delay_min, self.delay_max)
                t0 = time.monotonic()
                action, amount = await self.pool.act(m.id, h, BOT)
                await asyncio.sleep(max(0.0, target - (time.monotonic() - t0)))
                async with m.lock:
                    if m.status != "playing" or m.hand is not h or (m.hand_no, len(h.history)) != stamp:
                        m.bot_thinking = False
                        return
                    try:
                        h.act(BOT, action, amount)
                    except IllegalAction:
                        h.act(BOT, "check" if "check" in h.legal(BOT) else "fold")
                    m.bot_thinking = False
                    m.seq += 1
                    if h.finished:
                        await self._end_hand(m)
                await self.broadcast_state(m)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("bot turn loop failed")
            async with m.lock:
                m.bot_thinking = False

    # ------------------------------------------------------------ views
    @staticmethod
    def _actions(h: Hand) -> list[dict]:
        return [{"who": "you" if e["seat"] == HUMAN else "bot", "street": e["street"],
                 "action": e["action"], "amount": e["amount"]} for e in h.history]

    def _hand_record(self, m: Match, h: Hand) -> dict:
        r = h.result
        rec = {
            "hand_no": m.hand_no,
            "button": "you" if h.button == HUMAN else "bot",
            "your_cards": list(h.hole[HUMAN]),
            "bot_cards": list(h.hole[BOT]) if r.showdown else None,
            "board": list(h.board),
            "actions": self._actions(h),
            "pot": h.pot,
            "net": r.net[HUMAN],
            "showdown": r.showdown,
            "your_hand": hand_name(r.values[HUMAN]) if r.showdown else None,
            "bot_hand": hand_name(r.values[BOT]) if r.showdown else None,
            "folded": "you" if h.folded[HUMAN] else ("bot" if h.folded[BOT] else None),
        }
        return rec

    def view(self, m: Match) -> dict:
        """Everything the browser is allowed to know, from the human's seat."""
        h = m.hand
        your_turn = (m.status == "playing" and not h.finished and h.to_act == HUMAN and not m.bot_thinking)
        legal = {}
        if your_turn:
            lg = h.legal(HUMAN)
            legal = {"fold": "fold" in lg, "check": "check" in lg, "call": lg.get("call"),
                     "raise": lg.get("raise")}
        street_bets = sum(h.bets)
        state = {
            "match_id": m.id,
            "seq": m.seq,
            "status": m.status,
            "username": m.username,
            "hand_no": m.hand_no,
            "hands_total": m.hands_total,
            "big_blind": BIG_BLIND,
            "small_blind": SMALL_BLIND,
            "button": "you" if h.button == HUMAN else "bot",
            "street": h.street,
            "board": list(h.board),
            "pot": h.pot,
            "pot_middle": h.pot - (street_bets if not h.finished else 0),
            "you": {"stack": h.stacks[HUMAN], "bet": h.bets[HUMAN], "cards": list(h.hole[HUMAN]),
                    "folded": h.folded[HUMAN]},
            "bot": {"stack": h.stacks[BOT], "bet": h.bets[BOT], "folded": h.folded[BOT],
                    "cards": None},
            "to_act": None if h.finished else ("you" if h.to_act == HUMAN else
                                                 "bot" if h.to_act == BOT else None),
            "bot_thinking": m.bot_thinking,
            "your_turn": your_turn,
            "legal": legal,
            "to_call": h.to_call(HUMAN) if your_turn else 0,
            "actions": self._actions(h),
            "hand_over": h.finished,
            "result": None,
            "match": {"net_chips": m.net, "net_bb": m.net / BIG_BLIND, "hands_played": len(m.log),
                      "finished": m.status == "finished"},
            "history": m.log[-60:],
        }
        if h.finished:
            r = h.result
            rec = m.log[-1] if m.log and m.log[-1]["hand_no"] == m.hand_no else self._hand_record(m, h)
            state["result"] = {"net": r.net[HUMAN], "showdown": r.showdown, "folded": rec["folded"],
                               "your_hand": rec["your_hand"], "bot_hand": rec["bot_hand"],
                               "split": r.net[HUMAN] == 0 and r.showdown}
            if r.showdown:
                state["bot"]["cards"] = list(h.hole[BOT])
            # After the hand the stacks shown are the settled ones; bets are swept.
            state["you"]["bet"] = 0
            state["bot"]["bet"] = 0
        return state

    # ------------------------------------------------------------ sockets
    async def broadcast_state(self, m: Match):
        await self.broadcast(m, {"type": "state", "state": self.view(m)})

    async def broadcast(self, m: Match, msg: dict):
        dead = []
        for ws in list(m.sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            m.sockets.discard(ws)
