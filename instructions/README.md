# instructions/

This folder contains **templates and documentation** for instruction files.

## Personality files live in your Obsidian vault (outside this repo)

Mordecai loads personality/identity instructions using this priority order:

1) Per-user files from your configured Obsidian vault root (when configured):
	- `me/<TELEGRAM_ID>/soul.md`
	- `me/<TELEGRAM_ID>/id.md`

2) Built-in repo defaults (always available when running from the repo):
	- `instructions/soul.md`
	- `instructions/id.md`

Resolution order (per file):

1. Use the per-user file under `me/<TELEGRAM_ID>/` if it exists
2. Otherwise fallback to the repo default under `instructions/`

These files are automatically injected into the agent’s **system prompt** when
personality injection is enabled. Per-user overrides require `obsidian_vault_root`.

## Templates

- `instructions/soul.md` – example structure for a soul/personality file
- `instructions/id.md` – example identity metadata

If you want to bootstrap a new user, you can copy these templates into your
Obsidian vault under the user’s folder (`me/<TELEGRAM_ID>/`) and then edit.
