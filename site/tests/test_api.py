import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from app import main
from app.security import Signer, validate_username

CARD = re.compile(r'"([2-9TJQKA][cdhs])"')


def login(c, name):
    r = c.post("/api/session", json={"username": name})
    assert r.status_code == 200, r.text
    return r


def recv_state(ws):
    while True:
        m = ws.receive_json()
        if m["type"] == "state":
            return m["state"]
        if m["type"] == "error":
            return m


def wait_turn(ws):
    """Read frames until it is our move or the hand is over."""
    while True:
        m = ws.receive_json()
        if m["type"] != "state":
            continue
        s = m["state"]
        if s["your_turn"] or s["hand_over"] or s["status"] != "playing":
            return s


def play_match(c, ws):
    s = wait_turn(ws)
    while True:
        if s["status"] != "playing" or s["match"]["finished"]:
            return s
        if s["hand_over"]:
            ws.send_json({"type": "next", "seq": s["seq"]})
        else:
            ws.send_json({"type": "action", "action": "check" if s["legal"]["check"] else "call", "seq": s["seq"]})
        s = wait_turn(ws)


# ------------------------------------------------------------------ usernames & sessions

@pytest.mark.parametrize("name,ok", [
    ("ab", False), ("a" * 17, False), ("bad name", False), ("emoji😀", False), ("x-y", False),
    ("Sh1t_head", False), ("FUCKER", False), ("b0t", False), ("Admin", False), ("ass", False),
    ("river_rat", True), ("Grape_Ape", True), ("therapist", True), ("Document", True), ("ABC", True),
    ("user_123", True), ("Analyst99", True),
])
def test_username_rules(name, ok):
    assert (validate_username(name) is None) is ok


def test_session_token_tamper_and_expiry():
    s = Signer("k")
    sid, tok = s.issue()
    assert s.verify(tok, 100) == sid
    assert s.verify(tok[:-2] + "xx", 100) is None
    other = sid[:-1] + ("0" if sid[-1] != "0" else "1")
    assert s.verify(tok.replace(sid, other), 100) is None
    assert Signer("other").verify(tok, 100) is None
    assert s.verify(tok, -1) is None
    assert s.verify("garbage", 100) is None


def test_name_cannot_be_taken_by_another_session(client):
    login(client, "alice_1")
    with TestClient(main.app) as other:
        r = other.post("/api/session", json={"username": "ALICE_1"})
        assert r.status_code == 409
        # and cannot create a match under it either
        assert other.post("/api/matches", json={}).status_code == 401
    # the owner can re-claim it
    assert client.post("/api/session", json={"username": "alice_1"}).status_code == 200


def test_forged_cookie_rejected(client):
    client.cookies.set("pk_session", "0" * 32 + ".1.forged")
    assert client.post("/api/matches", json={}).status_code == 401


def test_post_requires_json_and_same_origin(client):
    assert client.post("/api/session", content="username=x",
                       headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415
    r = client.post("/api/session", json={"username": "evil_1"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


# ------------------------------------------------------------------ privacy

def test_server_files_never_served(client):
    for p in ["/bot.py", "/static/bot.py", "/static/../bot.py", "/static/%2e%2e/bot.py", "/app/main.py",
              "/static/../app/bot_worker.py", "/docs", "/openapi.json", "/redoc", "/static/"]:
        r = client.get(p)
        assert r.status_code == 404, p
        assert "class Agent" not in r.text and "import" not in r.text


def test_frames_never_reveal_bot_cards_or_deck(client):
    login(client, "privacy_1")
    client.post("/api/matches", json={})
    manager = main.state["manager"]
    with client.websocket_connect("/ws") as ws:
        seen_hands = 0
        while True:
            raw = ws.receive_text()
            msg = json.loads(raw)
            if msg["type"] != "state":
                continue
            s = msg["state"]
            m = manager.matches[s["match_id"]]
            h = m.hand
            cur = json.dumps({k: v for k, v in s.items() if k != "history"})
            cards = set(CARD.findall(cur))
            assert "deck" not in raw
            if not s["hand_over"]:
                assert s["bot"]["cards"] is None
                assert not cards & set(h.hole[1]), "bot hole card leaked"
                assert not cards & set(h.deck), "undealt card leaked"
            elif not s["result"]["showdown"]:
                assert s["bot"]["cards"] is None
            if s["status"] != "playing" or s["match"]["finished"]:
                break
            if s["hand_over"]:
                seen_hands += 1
                ws.send_json({"type": "next", "seq": s["seq"]})
            elif s["your_turn"]:
                ws.send_json({"type": "action", "action": "check" if s["legal"]["check"] else "call", "seq": s["seq"]})
        assert seen_hands == 2


@pytest.mark.parametrize("bot_client", ["crash", "garbage", "raise"], indirect=True)
def test_bot_errors_and_extra_fields_never_reach_client(bot_client):
    client = bot_client
    mode = os.environ["FAKE_BOT_MODE"]
    if True:
        login(client, f"err_{mode}")
        client.post("/api/matches", json={})
        with client.websocket_connect("/ws") as ws:
            frames = []
            s = wait_turn(ws)
            for _ in range(6):
                frames.append(json.dumps(s))
                if s["hand_over"] or s["status"] != "playing":
                    break
                ws.send_json({"type": "action", "action": "check" if s["legal"]["check"] else "call", "seq": s["seq"]})
                s = wait_turn(ws)
            frames.append(json.dumps(s))
        blob = "".join(frames)
        assert "SECRET_INTERNAL_VALUE" not in blob and "equity" not in blob and "Traceback" not in blob


@pytest.mark.parametrize("bot_client", ["slow"], indirect=True)
def test_slow_bot_times_out_and_game_continues(bot_client):
    client = bot_client
    if True:
        login(client, "slowpoke")
        client.post("/api/matches", json={})
        with client.websocket_connect("/ws") as ws:
            s = wait_turn(ws)
            # Hand 1: human is the button and acts first; calling hands the action to the bot.
            import time
            t0 = time.monotonic()
            ws.send_json({"type": "action", "action": "call", "seq": s["seq"]})
            s = wait_turn(ws)
            elapsed = time.monotonic() - t0
            acts = [a for a in s["actions"] if a["who"] == "bot" and a["action"] not in ("small_blind", "big_blind")]
            assert acts and acts[0]["action"] == "check"          # timeout fallback, game moved on
            assert 0.9 < elapsed < 5                              # BOT_TIMEOUT=1s, not the bot's 30s hang
            assert main.state["pool"].workers[0].generation >= 2  # hung worker was killed and replaced


# ------------------------------------------------------------------ action validation

def test_out_of_turn_illegal_and_malformed_actions(client):
    login(client, "rules_1")
    client.post("/api/matches", json={})
    with client.websocket_connect("/ws") as ws:
        s = wait_turn(ws)
        assert s["your_turn"] and s["legal"]["call"] == 10   # hand 1: human on the button
        bad = [
            {"type": "action", "action": "check", "seq": s["seq"]},            # facing the big blind
            {"type": "action", "action": "raise", "amount": 25, "seq": s["seq"]},   # below min raise
            {"type": "action", "action": "raise", "amount": 99999, "seq": s["seq"]},
            {"type": "action", "action": "raise", "amount": "100", "seq": s["seq"]},
            {"type": "action", "action": "raise", "amount": 50.5, "seq": s["seq"]},
            {"type": "action", "action": "allin", "seq": s["seq"]},
            {"type": "action", "action": "call", "seq": s["seq"] - 1},          # stale
            {"type": "next", "seq": s["seq"]},                                 # hand not over
            {"type": "score", "net_chips": 100000, "seq": s["seq"]},           # clients cannot report scores
        ]
        for msg in bad:
            ws.send_json(msg)
            err = ws.receive_json()
            assert err["type"] == "error", msg
            ws.receive_json()                                                  # resync state
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_text("[1,2]")
        assert ws.receive_json()["type"] == "error"
        ws.send_text("x" * 1000)
        assert ws.receive_json()["type"] == "error"
        # nothing above changed the hand
        ws.send_json({"type": "sync"})
        s2 = recv_state(ws)
        assert s2["actions"] == s["actions"]
        # a legal action works; then the bot moves and acting again is out of turn
        ws.send_json({"type": "action", "action": "call", "seq": s2["seq"]})
        s3 = recv_state(ws)
        assert not s3["your_turn"]
        ws.send_json({"type": "action", "action": "check", "seq": s3["seq"]})
        m = ws.receive_json()
        while m["type"] == "state":
            m = ws.receive_json()
        assert m["type"] == "error"


def test_cross_origin_websocket_rejected(client):
    login(client, "origin_1")
    client.post("/api/matches", json={})
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


# ------------------------------------------------------------------ scoring, abuse, expiry

def test_completed_match_scored_on_server_and_ranked(client):
    login(client, "scorer_1")
    client.post("/api/matches", json={})
    with client.websocket_connect("/ws") as ws:
        s = play_match(client, ws)
    assert s["status"] == "finished" and s["match"]["hands_played"] == 3
    lb = client.get("/api/leaderboard").json()
    me = [e for e in lb["entries"] if e["username"] == "scorer_1"]
    assert len(me) == 1
    assert me[0]["net_bb"] == s["match"]["net_chips"] / 20
    assert me[0]["played"] == 1 and me[0]["hands_played"] == 3
    assert lb["bot"]["matches"] >= 1


def test_quit_and_expired_matches_do_not_count(client):
    login(client, "quitter_1")
    client.post("/api/matches", json={})
    assert client.post("/api/matches/current/quit", json={}).status_code == 200
    client.post("/api/matches", json={})
    m = main.state["manager"].current(main._session(client))
    m.last_activity -= 10_000
    import asyncio
    client.portal.call(main.state["manager"].sweep) if hasattr(client, "portal") else asyncio.run(main.state["manager"].sweep())
    assert m.status == "abandoned"
    names = [e["username"] for e in client.get("/api/leaderboard").json()["entries"]]
    assert "quitter_1" not in names
    rows = main.db._q("SELECT status FROM matches WHERE username='quitter_1'")
    assert [r["status"] for r in rows] == ["abandoned", "abandoned"]


def test_match_creation_rate_limited_per_session(client):
    login(client, "spammer_1")
    codes = [client.post("/api/matches", json={}).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_match_creation_rate_limited_per_ip(client):
    main.match_ip_limit.hits.clear()
    limit = main.match_ip_limit.limit
    codes = []
    for i in range(limit + 2):
        with TestClient(main.app) as c:
            login(c, f"ipuser_{i}")
            codes.append(c.post("/api/matches", json={}).status_code)
    assert codes[:limit] == [200] * limit and codes[limit:] == [429, 429]


def test_bot_loaded_from_secret(monkeypatch):
    import base64
    import gzip
    import stat
    from pathlib import Path
    from app.bot_adapter import find_bot_path
    src = Path(__file__).with_name("fake_bot.py").read_bytes()
    monkeypatch.setenv("BOT_SOURCE_B64", base64.b64encode(gzip.compress(src)).decode())
    path = find_bot_path()
    assert Path(path).read_bytes() == src
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o400
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert "BOT_SOURCE_B64" not in os.environ          # removed so child processes don't inherit it
