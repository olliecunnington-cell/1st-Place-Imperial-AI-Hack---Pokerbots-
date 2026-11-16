"""Child process that hosts bot.py Agents. The only code that ever imports bot.py.

Protocol over a multiprocessing Pipe:
  request  (req_id, op, match_id, payload)   op in {"start", "act", "end", "drop"}
  response (req_id, ok, value)               value is an action dict for "act", else None

Only action dicts leave this process. Bot exceptions are swallowed here and reported
as a bare failure, so no bot internals, values or tracebacks reach the web process.
"""
from __future__ import annotations

import gc
import importlib.util
import os
import sys


def _load_bot(path: str):
    # Never leave a compiled copy of the bot (__pycache__/*.pyc) lying around on disk.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("private_bot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clean_action(r) -> dict | None:
    """Pass through only the two fields we need, with plain types."""
    if not isinstance(r, dict):
        return None
    a = r.get("action")
    if a not in ("fold", "check", "call", "raise"):
        return None
    out = {"action": a}
    if a == "raise":
        try:
            out["amount"] = int(r.get("amount"))
        except Exception:
            return None
    return out


def serve(conn, bot_path: str) -> None:
    # Bot output never reaches logs or clients.
    devnull = open(os.devnull, "w")
    sys.stdout = devnull
    sys.stderr = devnull
    try:
        mod = _load_bot(bot_path)
    except BaseException:
        conn.send((None, False, "load_failed"))
        return
    conn.send((None, True, "ready"))
    agents: dict[str, object] = {}
    calls = 0
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        req_id, op, match_id, payload = msg
        ok, value = True, None
        try:
            if op == "start":
                ag = mod.Agent()
                ag.on_table_start(payload)
                agents[match_id] = ag
            elif op == "act":
                value = _clean_action(agents[match_id].act(payload))
                ok = value is not None
            elif op == "end":
                agents[match_id].on_hand_end(payload)
            elif op == "drop":
                agents.pop(match_id, None)
        except BaseException:
            ok, value = False, None
        calls += 1
        # Periodic collection keeps a long-lived worker's memory bounded.
        if calls % 500 == 0:
            gc.collect()
        conn.send((req_id, ok, value))
