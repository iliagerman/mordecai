---
name: personality-editor
description: Edit the agent's personality (soul.md) and identity metadata (id.md) stored in the Obsidian vault, using safe, user-scoped paths keyed by Telegram ID.
---

# Personality Editor Skill

This skill explains how the agent modifies its own personality/identity **when the user asks**.

## Prerequisites / install & verify

- Prerequisite: the backend must be configured with an Obsidian vault root (the agent needs filesystem access to the vault).
- Verify configuration: ensure `obsidian_vault_root` is set (or `AGENT_OBSIDIAN_VAULT_ROOT` override is provided).
- Verify expected files:
   - Repo defaults exist: `instructions/soul.md`, `instructions/id.md`
   - Per-user files exist (or can be created in the vault): `me/<TELEGRAM_ID>/soul.md`, `me/<TELEGRAM_ID>/id.md`

Example (verification flow):

```text
personality_read(kind="soul", source="auto")
personality_read(kind="id", source="auto")
```

## Storage layout (Obsidian vault; NOT the repo workspace)

Vault root is configured by the backend (`obsidian_vault_root`, env override `AGENT_OBSIDIAN_VAULT_ROOT`).

Files:
- Per-user (Telegram id):
  - `me/<TELEGRAM_ID>/soul.md`
  - `me/<TELEGRAM_ID>/id.md`

- Defaults (built-in repo templates):
   - `instructions/soul.md`
   - `instructions/id.md`

Resolution order (per file):

1. If `me/<TELEGRAM_ID>/<file>.md` exists, it is used.
2. Else `instructions/<file>.md` is used.

## When the user asks to change the agent's personality

- The personality file to change is **always** the per-user file: `me/<TELEGRAM_ID>/soul.md`.
- Do NOT edit the repo defaults (`instructions/soul.md`) unless the operator explicitly asks to change the global defaults.

### Process

1. Clarify what should change (tone, rules, verbosity, boundaries).
2. Read current soul:
   - Use tool `personality_read(kind="soul", source="auto")`.
3. Propose a small diff (describe what will be changed).
4. After confirmation, write the updated soul:
   - Use tool `personality_write(kind="soul", content="...")`.

If the per-user file doesn't exist yet:
- Either write a new file directly (recommended), or
- Call `personality_reset_to_default(kind="soul")` first and then edit.

## When the user asks to change bot identity metadata

- Use `me/<TELEGRAM_ID>/id.md`.
- This file should contain:
  - bot name
  - bot age
  - geo location

### Process

1. Read current id:
   - `personality_read(kind="id", source="auto")`
2. Propose changes.
3. Write the updated id:
   - `personality_write(kind="id", content="...")`

## Safety & isolation rules

- Never read or write another user's folder.
- Never accept arbitrary filesystem paths; only use the personality tools.
- Keep edits minimal and explicit.
