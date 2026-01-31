# instructions/

This folder contains **templates and documentation** for instruction files.

## Personality files live in your Obsidian vault (outside this repo)

Mordecai loads personality/identity instructions from your configured Obsidian vault root:

- `me/default/soul.md`
- `me/default/id.md`
- `me/<TELEGRAM_ID>/soul.md`
- `me/<TELEGRAM_ID>/id.md`

Resolution order (per file):

1. Use the per-user file under `me/<TELEGRAM_ID>/` if it exists
2. Otherwise fallback to the default file under `me/default/`

These files are automatically injected into the agent’s **system prompt** when
`obsidian_vault_root` is configured.

## Templates

- `instructions/soul.md` – example structure for a soul/personality file
- `instructions/id.md` – example identity metadata

If you want to bootstrap a new user, you typically copy these templates into your
Obsidian vault under `me/default/` (or the user’s folder).
