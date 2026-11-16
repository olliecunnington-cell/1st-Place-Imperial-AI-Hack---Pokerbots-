FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DB_PATH=/data/poker.db \
    PORT=8080

WORKDIR /app
RUN useradd --create-home --uid 10001 poker && mkdir -p /data && chown poker:poker /data

COPY site/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY site/app/ ./app/
COPY site/static/ ./static/
# bot.py (repo root, git-ignored) is optional: the bracket glob lets the build succeed without it.
# Builds from GitHub never have it; the bot then comes from the BOT_SOURCE_B64 secret or
# Render's /etc/secrets/bot.py instead.
COPY site/requirements.txt bot.p[y] ./
RUN if [ -f bot.py ]; then chown poker:poker bot.py && chmod 0400 bot.py; fi
COPY site/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080
VOLUME ["/data"]
ENTRYPOINT ["docker-entrypoint.sh"]
# One process only: matches live in memory and SQLite has a single writer.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-server-header --log-level info"]
