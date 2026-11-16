# Heads-Up vs The Bot

A web app where people play heads-up no-limit Texas hold'em against a private, competition-winning
poker bot, with a public leaderboard.

- **Format:** blinds 10/20, 100bb (2,000 chip) stacks reset every hand, button alternates, 50 hands per match.
- **Stack:** FastAPI + WebSockets, SQLite, plain HTML/CSS/JS (no build step).

> ## ⚠️ Keep `bot.py` private
> `bot.py` is the competition bot and must never be published.
> - It is listed in `.gitignore`. Before pushing, run `git check-ignore bot.py` (it should print `bot.py`) and
>   `git status` (it must not appear). **Never use `git add -f bot.py`.**
> - If the repo is public, give the bot to the host as a secret file (Render) or deploy from your
>   machine (Fly). Both are described below.
> - A Docker image built from this folder **contains bot.py**. Never push that image to a public registry.
> - If `bot.py` was ever committed, removing it in a later commit is not enough: it stays in the history.
>   Rewrite the history (for example with `git filter-repo`) or keep that repository private.

## How the bot stays private

| Concern | What the app does |
|---|---|
| Source code | `bot.py` lives outside `static/`, the only folder served as files. Every other path is an explicit route. Tests check that `/bot.py`, `/app/*.py`, traversal attempts and the API docs pages all return 404. |
| Process isolation | Only a separate worker process imports `bot.py` (`app/bot_worker.py`). The web process never imports it. The worker's stdout/stderr go to `/dev/null`. |
| Bot output | The worker passes back only `{"action", "amount"}`. Extra keys, exceptions and tracebacks are dropped. The server re-validates every bot action against the engine. |
| Cards | The client view (`MatchManager.view`) is the one place that builds what the browser sees. It never includes the deck. The bot's hole cards are added only when the hand ends at showdown. |
| Timing | Each bot decision is padded to a random 0.7–1.4 s, so think time does not leak how close a decision was. |
| Hangs | Each bot call has a hard timeout (`BOT_TIMEOUT`, default 3 s). A hung worker is killed and restarted, and the bot checks or folds for that decision. |
| Errors | Production mode turns off debug, the `/docs` and `/openapi.json` pages, and server headers. Errors return a generic `{"error": "..."}`. |

## How it fits together

```
browser ──HTTP──▶ FastAPI (app/main.py) ── pages, /api/session, /api/matches, /api/leaderboard
        ◀─WS───▶ /ws ── MatchManager (app/matches.py) ── engine (app/engine.py): shuffle, deal, validate, settle
                                     │
                                     └── BotPool (app/bot_adapter.py) ──pipe──▶ worker process (app/bot_worker.py) ──▶ bot.py
```

- `app/engine.py`: the rules. A `Hand` deals from a `secrets.SystemRandom` shuffle, lists legal actions
  (min-raise, all-in, and short all-ins that don't reopen betting) and rejects anything else. It settles
  main and side pots, split pots (odd chip left of the button) and returns uncalled bets. Its hand evaluator
  is independent of the bot's.
- `app/bot_adapter.py`: translates engine state into the dict `bot.py` expects (`act(state)`,
  `on_table_start(info)`, `on_hand_end(summary)`). It runs one `Agent` per match. `bot.py` is unmodified.
- `app/matches.py`: match lifecycle, bot turns, expiry, and the client view.
- `app/security.py`: HMAC-signed session tokens, username rules and profanity filter, sliding-window rate limits.
- `app/db.py`: SQLite storage. Scores are computed on the server from the engine's results. There is no endpoint
  that accepts a score.

### Sessions, names and the leaderboard

- The landing page asks for a username: 3–16 letters, digits or `_`, passing a profanity filter (with
  leetspeak normalisation) and not a reserved name such as `bot` or `admin`.
- The browser gets an `HttpOnly`, `SameSite=Lax` (and `Secure` in production) cookie holding a random session
  id signed with `SECRET_KEY`. Names are tied to the session that claimed them. Another browser can't use
  that name until it has been inactive for `NAME_HOLD_DAYS` (default 30). Clearing cookies loses the name for that long.
- Only **completed** matches are stored as results. Quitting, starting a new match, `MATCH_IDLE_SECONDS`
  (default 600) without a player action, or a server restart marks a match `abandoned`. Abandoned matches
  never reach the leaderboard or the bot record.
- The leaderboard ranks each player's **best** completed match by net big blinds, alongside bb/100 and their
  number of completed matches. It only ranks matches of the current length (`MATCH_HANDS`). The bot's
  record counts every completed match against every player.

### Abuse protection

- Match creation: 5 per session per 15 minutes and 20 per IP per hour. New sessions: 30 per IP per hour.
  API calls: 120 per IP per minute. WebSocket messages: 12 per 2 seconds per connection. One active match per
  session, and at most `MAX_ACTIVE_MATCHES` (200) running at once.
- Every WebSocket message must carry the current state `seq`, so stale, duplicate and out-of-turn actions
  are rejected. So are malformed JSON, unknown actions, non-integer or out-of-range raises, and oversized messages.
- POST requests must be JSON. POST requests and WebSocket connections must come from the same origin.
  Pages send a strict Content-Security-Policy, `X-Frame-Options: DENY` and `nosniff`.

## Run locally

Requires Python 3.11+ (tested on 3.13).

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

Put `bot.py` in the project root (next to this README), then:

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. Without `SECRET_KEY`, development mode uses a random key, so sessions reset when
the server restarts. Data goes to `data/poker.db`.

### Tests

```bash
pytest -q
```

The tests use `tests/fake_bot.py`, a stand-in with the same interface, so they never need `bot.py` and can run in
CI. They cover hand ranking, split and side pots, all-ins, uncalled bets, legal action generation, chip
conservation over random play, illegal and out-of-turn actions, card privacy in every WebSocket frame, bot
crashes, garbage output, timeouts, rate limits, name ownership, and leaderboard scoring.

### Run with Docker

```bash
docker build -t headsup-bot .
```

```bash
docker run -p 8000:8000 -e SECRET_KEY="$(openssl rand -hex 32)" -e COOKIE_SECURE=0 -v headsup_data:/data headsup-bot
```

`COOKIE_SECURE=0` is only for plain `http://localhost`. Leave it unset behind HTTPS. The container runs as an
unprivileged user and keeps SQLite in `/data`, so mount a volume there.

## Deploy

The app keeps live matches in memory and uses SQLite, so **run exactly one instance**. Do not scale out. Give it
persistent storage at `/data`.

### Fly.io

`fly deploy` builds from your local folder (it honours `.dockerignore`, which keeps `bot.py` in the build), so
`bot.py` never needs to be in git. The image goes to your account's private Fly registry.

1. Install `flyctl` and log in, then change `app` (and optionally `primary_region`) in `fly.toml`.
2. Create the app without deploying:
   ```bash
   fly launch --no-deploy --copy-config
   ```
3. Create the volume for the database (same region as `primary_region`):
   ```bash
   fly volumes create poker_data --size 1 --region lhr
   ```
4. Set the signing key:
   ```bash
   fly secrets set SECRET_KEY="$(openssl rand -hex 32)"
   ```
5. Deploy, with `bot.py` present in the folder:
   ```bash
   fly deploy
   ```

`fly.toml` already sets `CLIENT_IP_HEADER=Fly-Client-IP` for per-IP rate limits and keeps one machine running.

#### Deploying from GitHub through the Fly dashboard

Fly's GitHub launcher builds from the repo, which has no `bot.py`, so supply the bot as a secret instead. In
the app's **Secrets** page add:

- `SECRET_KEY`: any long random string, for example from `openssl rand -hex 32`.
- `BOT_SOURCE_B64`: `bot.py` gzipped and base64-encoded, made on your own machine with:
  ```bash
  python3 -c "import gzip,base64,sys;print(base64.b64encode(gzip.compress(open('bot.py','rb').read(),9)).decode())"
  ```

At startup the app writes the bot to a private temporary file and removes the secret from its environment.
Without both secrets the server refuses to start, and Fly reports that it can't find machines to route to.

### Render

Render builds from your git repo, which must not contain `bot.py`. Instead, give the bot to the service as a
**Secret File**. The app finds it at `/etc/secrets/bot.py` at runtime.

1. Push this repo to GitHub (check `git status` first: `bot.py` must not be listed).
2. In Render choose **New → Blueprint** and select the repo. `render.yaml` creates a Docker web service with a
   1 GB disk at `/data`, a generated `SECRET_KEY` and one instance. Persistent disks need a paid instance type.
3. In the service's **Environment** settings add a **Secret File** named `bot.py` and paste the bot's source.
4. Deploy. `/healthz` is the health check.

`render.yaml` sets `TRUSTED_PROXY_HOPS=1`, which takes the client IP from the last `X-Forwarded-For` entry. A
client cannot spoof that entry. If several users turn out to share an IP in the logs, adjust this setting.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | required in production | Signs session cookies. |
| `APP_ENV` | `development` (`production` in Docker) | Production refuses to start without `SECRET_KEY` and enables secure cookies. |
| `DB_PATH` | `data/poker.db` (`/data/poker.db` in Docker) | SQLite file. |
| `BOT_PATH` | `$BOT_PATH`, `/etc/secrets/bot.py`, then `./bot.py` | Where to load the bot from. |
| `MATCH_HANDS` | `50` | Hands per match. |
| `BOT_DELAY_MIN` / `BOT_DELAY_MAX` | `0.7` / `1.4` | Seconds before the bot acts. |
| `BOT_TIMEOUT` | `3.0` | Hard limit per bot call. |
| `BOT_WORKERS` | `2` | Bot worker processes. |
| `MATCH_IDLE_SECONDS` | `600` | Inactivity before a match is abandoned. |
| `NAME_HOLD_DAYS` | `30` | How long a name stays reserved for its session. |
| `MAX_ACTIVE_MATCHES` | `200` | Concurrent match cap. |
| `MATCH_PER_SESSION_PER_15MIN`, `MATCH_PER_IP_PER_HOUR`, `SESSIONS_PER_IP_PER_HOUR`, `API_PER_IP_PER_MIN`, `WS_MSGS_PER_2S` | 5, 20, 30, 120, 12 | Rate limits. |
| `CLIENT_IP_HEADER` / `TRUSTED_PROXY_HOPS` | unset / `0` | How to find the client IP behind a proxy. |
| `ALLOWED_HOSTS` | empty | Extra hosts accepted in the `Origin` check (for example a custom domain behind a proxy that rewrites `Host`). |
| `COOKIE_SECURE` | `1` in production | Set `0` only for plain-HTTP local runs. |

## Known limitations

- A player can quit a losing match before it completes. That match doesn't count, but neither does it count
  against them, and only completed matches go into the bot's record. Rate limits cap how often this can happen.
- Live matches are lost on restart or redeploy: they are marked abandoned, completed results are kept.
- Bot speed depends on the host CPU. Use a dedicated CPU if decisions seem rushed on shared hardware.
