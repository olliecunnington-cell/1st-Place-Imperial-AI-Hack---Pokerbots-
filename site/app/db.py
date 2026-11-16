"""SQLite storage for usernames and matches. Only server-computed results are written."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username_key TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    created_at   REAL NOT NULL,
    last_seen    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    username_key  TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    status        TEXT NOT NULL,          -- active | completed | abandoned
    hands_total   INTEGER NOT NULL,
    hands_played  INTEGER NOT NULL DEFAULT 0,
    net_chips     INTEGER NOT NULL DEFAULT 0,
    net_bb        REAL NOT NULL DEFAULT 0,
    bb_per_100    REAL NOT NULL DEFAULT 0,
    big_blind     INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    finished_at   REAL
);
CREATE INDEX IF NOT EXISTS matches_status_user ON matches(status, username_key);
"""


def init(path: str | None = None) -> None:
    global _conn
    path = path or os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "poker.db"))
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    if path != ":memory:":
        _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA)
    # Anything still "active" belonged to a previous process and can never finish.
    _conn.execute("UPDATE matches SET status='abandoned', finished_at=? WHERE status='active'", (time.time(),))


def _q(sql: str, args=()):
    with _lock:
        return _conn.execute(sql, args).fetchall()


# ------------------------------------------------------------------ users

def claim_username(username: str, session_id: str, hold_seconds: float) -> bool:
    """Claim a name for this session. Fails if another session used it recently."""
    key = username.lower()
    now = time.time()
    with _lock:
        row = _conn.execute("SELECT session_id, last_seen FROM users WHERE username_key=?", (key,)).fetchone()
        if row and row["session_id"] != session_id and now - row["last_seen"] < hold_seconds:
            return False
        _conn.execute(
            "INSERT INTO users(username_key, username, session_id, created_at, last_seen) VALUES (?,?,?,?,?) "
            "ON CONFLICT(username_key) DO UPDATE SET username=excluded.username, "
            "session_id=excluded.session_id, last_seen=excluded.last_seen",
            (key, username, session_id, now, now))
        return True


def owns_username(username: str, session_id: str) -> bool:
    rows = _q("SELECT 1 FROM users WHERE username_key=? AND session_id=?", (username.lower(), session_id))
    return bool(rows)


def touch_user(username: str, session_id: str) -> None:
    _q("UPDATE users SET last_seen=? WHERE username_key=? AND session_id=?",
       (time.time(), username.lower(), session_id))


# ------------------------------------------------------------------ matches

def create_match(match_id: str, username: str, session_id: str, hands_total: int, big_blind: int) -> None:
    _q("INSERT INTO matches(id, username, username_key, session_id, status, hands_total, big_blind, created_at) "
       "VALUES (?,?,?,?, 'active', ?,?,?)",
       (match_id, username, username.lower(), session_id, hands_total, big_blind, time.time()))


def finish_match(match_id: str, status: str, hands_played: int, net_chips: int, big_blind: int) -> None:
    assert status in ("completed", "abandoned")
    net_bb = net_chips / big_blind
    bb100 = net_bb * 100.0 / hands_played if hands_played else 0.0
    _q("UPDATE matches SET status=?, hands_played=?, net_chips=?, net_bb=?, bb_per_100=?, finished_at=? "
       "WHERE id=? AND status='active'",
       (status, hands_played, net_chips, net_bb, bb100, time.time(), match_id))


def leaderboard(hands_total: int, limit: int = 100) -> list[dict]:
    """Best completed match per player, only among matches of the current length."""
    rows = _q("""
        WITH c AS (SELECT * FROM matches WHERE status='completed' AND hands_total=?),
        ranked AS (
            SELECT c.*, ROW_NUMBER() OVER (PARTITION BY username_key
                       ORDER BY net_chips DESC, finished_at ASC) AS rn,
                   COUNT(*) OVER (PARTITION BY username_key) AS played
            FROM c)
        SELECT username, net_bb, bb_per_100, hands_played, played, finished_at
        FROM ranked WHERE rn=1
        ORDER BY net_bb DESC, finished_at ASC
        LIMIT ?""", (hands_total, limit))
    return [dict(r) for r in rows]


def bot_record() -> dict:
    r = _q("""SELECT COUNT(*) AS matches, COALESCE(SUM(hands_played),0) AS hands,
                     COALESCE(SUM(net_chips),0) AS human_net,
                     COALESCE(SUM(net_chips < 0),0) AS bot_wins,
                     COALESCE(SUM(net_chips > 0),0) AS bot_losses,
                     COALESCE(SUM(net_chips = 0),0) AS ties,
                     COUNT(DISTINCT username_key) AS players,
                     MAX(big_blind) AS bb
              FROM matches WHERE status='completed'""")[0]
    bb = r["bb"] or 20
    bot_net = -r["human_net"]
    return {
        "matches": r["matches"], "hands": r["hands"], "players": r["players"],
        "wins": r["bot_wins"], "losses": r["bot_losses"], "ties": r["ties"],
        "net_bb": bot_net / bb,
        "bb_per_100": (bot_net / bb) * 100.0 / r["hands"] if r["hands"] else 0.0,
    }
