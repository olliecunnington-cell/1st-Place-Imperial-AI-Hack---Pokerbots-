#!/bin/sh
# Mounted volumes (Fly, Render disks, docker -v) arrive owned by root. Fix ownership of the
# data directory, then drop privileges so the app never runs as root.
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$(dirname "${DB_PATH:-/data/poker.db}")"
    chown -R poker:poker "$(dirname "${DB_PATH:-/data/poker.db}")"
    exec setpriv --reuid=poker --regid=poker --init-groups "$@"
fi
exec "$@"
