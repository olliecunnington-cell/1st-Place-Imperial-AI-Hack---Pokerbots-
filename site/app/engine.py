"""Heads-up no-limit hold'em engine. The server's single source of truth.

Independent of bot.py: it shuffles, deals, validates every action, and settles pots.
Chip amounts are integers. A "raise" amount is always the total the player's bet
becomes on this street (raise-to), which is also how bot.py reports it.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "cdhs"
FULL_DECK = [r + s for r in RANKS for s in SUITS]
STREETS = ("preflop", "flop", "turn", "river")

HAND_NAMES = (
    "High card", "One pair", "Two pair", "Three of a kind", "Straight",
    "Flush", "Full house", "Four of a kind", "Straight flush",
)

_sysrand = secrets.SystemRandom()


class IllegalAction(ValueError):
    """Raised for out-of-turn, illegal or malformed actions."""


# ------------------------------------------------------------------ hand ranking

def _rank5(cards: tuple[str, ...]) -> tuple:
    vals = sorted((RANKS.index(c[0]) for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    uniq = sorted(set(vals), reverse=True)
    straight_high = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:      # A-2-3-4-5 wheel
            straight_high = 3
    counts: dict[int, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    ordered = tuple(v for v, _ in groups)
    shape = tuple(n for _, n in groups)
    if straight_high is not None and flush:
        return (8, straight_high)
    if shape == (4, 1):
        return (7,) + ordered
    if shape == (3, 2):
        return (6,) + ordered
    if flush:
        return (5,) + tuple(vals)
    if straight_high is not None:
        return (4, straight_high)
    if shape == (3, 1, 1):
        return (3,) + ordered
    if shape == (2, 2, 1):
        return (2,) + ordered
    if shape == (2, 1, 1, 1):
        return (1,) + ordered
    return (0,) + tuple(vals)


def best_hand(cards: list[str]) -> tuple:
    """Best 5-card value from 5 to 7 cards. Larger tuples win; equal tuples split."""
    if not 5 <= len(cards) <= 7:
        raise ValueError("need 5 to 7 cards")
    return max(_rank5(c) for c in combinations(cards, 5))


def hand_name(value: tuple) -> str:
    return HAND_NAMES[value[0]]


# ------------------------------------------------------------------ pots

def settle_pots(committed: list[int], folded: list[bool], values: list[tuple | None],
                odd_chip_order: list[int]) -> list[int]:
    """Split everything committed into main/side pots and award each one.

    committed: total chips each seat put in this hand. values: hand value per seat
    (None for folded seats). odd_chip_order: seats in the order that receives odd
    chips from a split (first seat left of the button first). Returns chips won per seat.
    """
    n = len(committed)
    won = [0] * n
    levels = sorted({c for c in committed if c > 0})
    prev = 0
    for lvl in levels:
        contributors = [i for i in range(n) if committed[i] >= lvl]
        pot = (lvl - prev) * len(contributors)
        prev = lvl
        eligible = [i for i in contributors if not folded[i]]
        if not eligible:
            # Every contributor at this level folded: it goes to the best live hand.
            eligible = [i for i in range(n) if not folded[i]]
        if len(eligible) == 1:
            won[eligible[0]] += pot
            continue
        best = max(values[i] for i in eligible)
        winners = [i for i in eligible if values[i] == best]
        share, odd = divmod(pot, len(winners))
        for i in winners:
            won[i] += share
        for i in [s for s in odd_chip_order if s in winners][:odd]:
            won[i] += 1
    return won


# ------------------------------------------------------------------ hand state

@dataclass
class HandResult:
    winners: list[int]
    won: list[int]               # chips pushed to each seat (includes returned uncalled bets)
    net: list[int]               # change in stack per seat for this hand
    showdown: bool
    values: list[tuple | None]
    reason: str                  # "fold" or "showdown"


@dataclass
class Hand:
    """One hand between seat 0 and seat 1. The button posts the small blind."""

    stacks: list[int]
    button: int
    small_blind: int = 10
    big_blind: int = 20
    deck: list[str] | None = None

    hole: list[list[str]] = field(default_factory=list)
    board: list[str] = field(default_factory=list)
    street: str = "preflop"
    bets: list[int] = field(default_factory=lambda: [0, 0])
    committed: list[int] = field(default_factory=lambda: [0, 0])
    folded: list[bool] = field(default_factory=lambda: [False, False])
    history: list[dict] = field(default_factory=list)
    to_act: int | None = None
    last_raise: int = 0
    raises_this_street: int = 0
    acted: set = field(default_factory=set)
    can_reraise: list[bool] = field(default_factory=lambda: [True, True])
    result: HandResult | None = None

    def __post_init__(self):
        if self.deck is None:
            self.deck = FULL_DECK[:]
            _sysrand.shuffle(self.deck)
        else:
            self.deck = list(self.deck)
        self.start_stacks = list(self.stacks)
        self.stacks = list(self.stacks)
        sb, bb = self.button, 1 - self.button
        # Heads-up deal: one card each, starting left of the button (the big blind).
        c = [self._draw() for _ in range(4)]
        self.hole = [None, None]
        self.hole[bb] = [c[0], c[2]]
        self.hole[sb] = [c[1], c[3]]
        self._post(sb, self.small_blind, "small_blind")
        self._post(bb, self.big_blind, "big_blind")
        self.last_raise = self.big_blind
        self.to_act = sb
        self._advance_if_no_betting()

    # -- helpers
    def _draw(self) -> str:
        return self.deck.pop()

    def _post(self, seat: int, amount: int, kind: str):
        amt = min(amount, self.stacks[seat])
        self.stacks[seat] -= amt
        self.bets[seat] += amt
        self.committed[seat] += amt
        self.history.append({"seat": seat, "street": "preflop", "action": kind, "amount": amt})

    @property
    def finished(self) -> bool:
        return self.result is not None

    @property
    def pot(self) -> int:
        """Everything in the middle, including this street's bets."""
        return sum(self.committed)

    def to_call(self, seat: int) -> int:
        return max(0, max(self.bets) - self.bets[seat])

    def legal(self, seat: int) -> dict:
        """Legal actions for a seat. Empty if it is not that seat's turn."""
        if self.finished or self.to_act != seat:
            return {}
        opp = 1 - seat
        tc = self.to_call(seat)
        out: dict = {}
        if tc > 0:
            out["fold"] = True
            out["call"] = min(tc, self.stacks[seat])
        else:
            out["check"] = True
        if self.stacks[seat] > tc and self.stacks[opp] > 0 and self.can_reraise[seat]:
            cur = max(self.bets)
            max_to = self.bets[seat] + self.stacks[seat]
            min_to = min(cur + max(self.last_raise, self.big_blind), max_to)
            out["raise"] = {"min": min_to, "max": max_to, "kind": "raise" if cur > 0 else "bet"}
        return out

    def act(self, seat: int, action: str, amount: int | None = None) -> None:
        """Apply an action. Raises IllegalAction for anything not currently legal."""
        if self.finished:
            raise IllegalAction("hand is over")
        if seat != self.to_act:
            raise IllegalAction("not your turn")
        legal = self.legal(seat)
        if action not in legal:
            raise IllegalAction(f"{action} is not legal now")
        opp = 1 - seat
        street = self.street
        if action == "fold":
            self.folded[seat] = True
            self.history.append({"seat": seat, "street": street, "action": "fold", "amount": 0})
            self._finish()
            return
        if action == "check":
            self.history.append({"seat": seat, "street": street, "action": "check", "amount": 0})
            self.acted.add(seat)
        elif action == "call":
            amt = legal["call"]
            self.stacks[seat] -= amt
            self.bets[seat] += amt
            self.committed[seat] += amt
            self.history.append({"seat": seat, "street": street, "action": "call", "amount": amt})
            self.acted.add(seat)
        elif action == "raise":
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise IllegalAction("raise needs an integer amount")
            r = legal["raise"]
            if not r["min"] <= amount <= r["max"]:
                raise IllegalAction(f"raise must be between {r['min']} and {r['max']}")
            cur = max(self.bets)
            add = amount - self.bets[seat]
            self.stacks[seat] -= add
            self.bets[seat] = amount
            self.committed[seat] += add
            size = amount - cur
            full = size >= max(self.last_raise, self.big_blind)
            if full:
                self.last_raise = size
                self.can_reraise = [True, True]
            else:
                # An all-in for less than a full raise does not reopen betting for a
                # player who has already acted on this street.
                self.can_reraise[opp] = opp not in self.acted
            self.raises_this_street += 1
            self.history.append({"seat": seat, "street": street, "action": "raise", "amount": amount})
            self.acted = {seat}
        self._after_action(seat)

    def _after_action(self, seat: int):
        opp = 1 - seat
        matched = self.bets[0] == self.bets[1] or any(
            self.stacks[i] == 0 and self.bets[i] < max(self.bets) for i in (0, 1))
        street_closed = matched and (
            self.acted >= {0, 1}
            or (self.stacks[opp] == 0 and self.bets[seat] >= self.bets[opp])
        )
        if not street_closed:
            self.to_act = opp
            return
        self._next_street()

    def _betting_possible(self) -> bool:
        return all(s > 0 for s in self.stacks)

    def _next_street(self):
        if self.street == "river":
            self._finish()
            return
        idx = STREETS.index(self.street) + 1
        self.street = STREETS[idx]
        self.board += [self._draw() for _ in range(3 if self.street == "flop" else 1)]
        self.bets = [0, 0]
        self.acted = set()
        self.last_raise = self.big_blind
        self.raises_this_street = 0
        self.can_reraise = [True, True]
        self.to_act = 1 - self.button          # big blind acts first after the flop
        self._advance_if_no_betting()

    def _advance_if_no_betting(self):
        """With a player all-in and nothing left to call, run out the board."""
        if self.finished:
            return
        if self._betting_possible():
            return
        if self.to_call(self.to_act) > 0 and self.stacks[self.to_act] > 0:
            return                              # still has a decision: call or fold the all-in
        self.to_act = None
        while not self.finished:
            self._next_street() if self.street != "river" else self._finish()

    def _finish(self):
        self.to_act = None
        live = [i for i in (0, 1) if not self.folded[i]]
        if len(live) == 1:
            w = live[0]
            won = [0, 0]
            won[w] = self.pot
            values = [None, None]
            showdown = False
            reason = "fold"
        else:
            values = [best_hand(self.hole[i] + self.board) for i in (0, 1)]
            won = settle_pots(self.committed, self.folded, values, [1 - self.button, self.button])
            showdown = True
            reason = "showdown"
        for i in (0, 1):
            self.stacks[i] += won[i]
        net = [self.stacks[i] - self.start_stacks[i] for i in (0, 1)]
        winners = [i for i in (0, 1) if net[i] > 0] or [0, 1]
        self.result = HandResult(winners=winners, won=won, net=net, showdown=showdown,
                                 values=values, reason=reason)
