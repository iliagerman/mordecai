# Mordecai ↔ Vibe-Kanban shared SQLite directory

This repo can be run so that the Mordecai container has **direct read/write file access** to Vibe-Kanban's persisted SQLite DB directory.

## What gets shared

- Host directory: `/home/ilia/.local/share/vibe-kanban/`
- SQLite DB file inside it: `/home/ilia/.local/share/vibe-kanban/db.sqlite`

Inside the Mordecai container the directory is mounted at the **same path**:

- `/home/ilia/.local/share/vibe-kanban/`

## Why we run as UID 1000

Vibe-Kanban runs its app process as user `ilia` (UID 1000) in your infra setup.
To avoid creating **root-owned** files in the shared host directory, we run Mordecai as your host UID/GID.

## How to run

1) Ensure `.env` exists in this directory and matches your host user:

- `HOST_UID=1000`
- `HOST_GID=1000`

2) Start Mordecai using the override compose file:

- `docker compose -f docker-compose.yml -f docker-compose.vibe-kanban.yml up -d`

3) Confirm the mount exists in the running container:

- The directory should be present: `/home/ilia/.local/share/vibe-kanban/`

## Troubleshooting

- If you see permission errors, check ownership of `/home/ilia/.local/share/vibe-kanban` on the host.
- SQLite concurrency is generally fine for occasional writes, but multiple writers may cause transient `database is locked` errors.
