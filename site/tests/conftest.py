import os
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update({
    "BOT_PATH": str(Path(__file__).with_name("fake_bot.py")),
    "DB_PATH": os.path.join(_tmp, "test.db"),
    "SECRET_KEY": "test-secret",
    "MATCH_HANDS": "3",
    "BOT_DELAY_MIN": "0",
    "BOT_DELAY_MAX": "0",
    "BOT_TIMEOUT": "1.0",
    "BOT_WORKERS": "1",
    "APP_ENV": "development",
    "WS_MSGS_PER_2S": "1000",
})


def _client(mode):
    from fastapi.testclient import TestClient
    from app import main
    for rl in (main.match_ip_limit, main.match_session_limit, main.session_ip_limit, main.api_ip_limit):
        rl.hits.clear()
    os.environ["FAKE_BOT_MODE"] = mode          # read by the bot worker when it spawns
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        os.environ["FAKE_BOT_MODE"] = "call"


@pytest.fixture()
def client():
    yield from _client("call")


@pytest.fixture()
def bot_client(request):
    """Client whose bot behaves per the test's parametrized mode."""
    yield from _client(request.param)
