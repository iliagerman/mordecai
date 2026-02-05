---
name: onboard-pending-skills
description: Review and onboard skills staged in pending/ folders (shared and per-user).
---

# Onboard Pending Skills

## Goal

When the user asks to **onboard pending skills**, you will:

1. List what is currently in the pending folders.
2. Run onboarding (validation + per-skill dependency install + promotion out of `pending/`).
3. Summarize what was onboarded, what failed (with `FAILED.json`), and what was skipped.

Pending skills are staged under:
- `<SHARED_SKILLS_DIR>/pending/<skill>/` (optional; only if shared pending is enabled)
- `<USER_SKILLS_DIR>/pending/<skill>/`

Where:
- `<USER_SKILLS_DIR>` is the runtime directory for the current user’s installed skills
	(a subdirectory of the configured `skills_base_dir`).
- `<SHARED_SKILLS_DIR>` is the configured shared skills directory (if used).

## Procedure

### 1) Preview

Call:
- `list_pending_skills(scope="all")`

Example:

```text
list_pending_skills(scope="all")
```

If nothing is pending, tell the user there is nothing to onboard and stop.

### 2) Dry run (recommended)

Call:
- `onboard_pending_skills(scope="all", dry_run=true)`

Example:

```text
onboard_pending_skills(scope="all", dry_run=true)
```

Show the user what would be promoted.

### 3) Onboard

This will:

1. Review the Python scripts inside each pending skill folder.
2. Infer missing dependencies and generate/extend `requirements.txt`.
3. Create a per-skill venv at `<skill>/.venv/` (only when needed).
4. Install the dependencies using `uv`.
5. Attempt to **run a small set of skill scripts** (smoke test) to catch runtime missing dependencies (for example, dynamic imports).

If a skill fails at any stage, it will contain `FAILED.json` and an `ONBOARDING_REPORT.md`.

### 4) If onboarding fails due to missing dependencies

If `onboard_pending_skills` reports failures:

1. Open the failing skill folder under `pending/`.
2. Read `FAILED.json` and `ONBOARDING_REPORT.md`.

If `FAILED.json.stage` is `run_scripts_smoke_test` and it includes `details.missing_modules`, you must install the missing dependency(ies) using the **Dependency Installer** skill:

- Call `install_package(package="<missing>", manager="uv")`

Then rerun onboarding:

- `onboard_pending_skills(scope="all", dry_run=false)`

If the user confirms (or explicitly asks to proceed), call:
- `onboard_pending_skills(scope="all", dry_run=false)`

Example:

```text
onboard_pending_skills(scope="all", dry_run=false)
```

## Rules

- Do not manually move files with shell commands; use the onboarding tool.
- If onboarding fails for a skill, it will contain `FAILED.json`. Point the user to that file and the `ONBOARDING_REPORT.md` inside the skill folder.
- Do not claim a skill is onboarded unless the tool reports it as `onboarded`.
