"""Tools for onboarding pending skills.

These tools allow the agent to list and onboard skills staged under pending/
folders (shared and/or per-user).

Context is injected by AgentService.
"""

# ruff: noqa: I001

from __future__ import annotations

import json
from typing import Literal

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


_pending_skill_service = None
_current_user_id: str | None = None


def set_pending_skill_context(pending_skill_service, user_id: str) -> None:
    global _pending_skill_service, _current_user_id
    _pending_skill_service = pending_skill_service
    _current_user_id = user_id


@tool(
    name="list_pending_skills",
    description=(
        "List skills waiting in pending/ folders (shared and/or user). "
        "Use this before onboarding to preview what will be processed."
    ),
)
def list_pending_skills(
    scope: Literal["user", "shared", "all"] = "all",
) -> str:
    if _pending_skill_service is None:
        return "Pending skill service not available."
    if _current_user_id is None:
        return "User context not available."

    include_shared = scope in ("shared", "all")
    include_user = scope in ("user", "all")

    items = []
    if include_shared:
        items.extend(_pending_skill_service.list_pending(user_id=None, include_shared=True))
    if include_user:
        items.extend(_pending_skill_service.list_pending(user_id=_current_user_id, include_shared=False))

    if not items:
        return "No pending skills found."

    lines = ["Pending skills:", ""]
    for c in items:
        where = "shared" if c.scope == "shared" else f"user:{_current_user_id}"
        lines.append(f"- {c.skill_name} ({where})")
    return "\n".join(lines)


@tool(
    name="onboard_pending_skills",
    description=(
        "Validate and onboard pending skills into active skill folders. "
        "This will normalize SKILL.md, compile-check Python, install per-skill dependencies, "
        "attempt to run a small set of skill scripts (smoke test), and then promote the skill out of pending/. "
        "Writes FAILED.json on validation failures. Note: already-installed skills are reported as skipped (not failed). "
        "AI review is required before real onboarding: pass ai_review_completed=true."
    ),
)
def onboard_pending_skills(
    scope: Literal["user", "shared", "all"] = "all",
    dry_run: bool = False,
    ai_review_completed: bool = False,
) -> str:
    if _pending_skill_service is None:
        return "Pending skill service not available."
    if _current_user_id is None:
        return "User context not available."

    if not dry_run and not ai_review_completed:
        return (
            "AI review is required before onboarding.\n\n"
            "Please:\n"
            "1) Call list_pending_skills(scope=\"all\")\n"
            "2) Review each pending skill's SKILL.md and scripts (file_read), make any fixes\n"
            "3) If runtime dependencies are missing, use dependency_installer.install_package(...)\n"
            "4) Re-run onboarding with ai_review_completed=true\n\n"
            "Example: onboard_pending_skills(scope=\"all\", dry_run=false, ai_review_completed=true)"
        )

    result = _pending_skill_service.onboard_pending(
        user_id=_current_user_id,
        scope=scope,
        dry_run=dry_run,
    )

    # Human-friendly summary first
    summary_lines = [
        "Pending skill onboarding results:",
        f"- total: {result.get('total')}",
        f"- onboarded: {result.get('onboarded')}",
        f"- skipped: {result.get('skipped')}",
        f"- failed: {result.get('failed')}",
    ]

    # Compact details
    details = result.get("results", [])
    if details:
        summary_lines.append("")
        summary_lines.append("Details:")
        for item in details:
            name = item.get("candidate")
            status = item.get("status")
            extra = item.get("path") or item.get("reason") or item.get("would_move_to") or item.get("error")
            if extra:
                summary_lines.append(f"- {name}: {status} ({extra})")
            else:
                summary_lines.append(f"- {name}: {status}")

    # Include JSON payload at the end (useful for debugging)
    summary_lines.append("")
    summary_lines.append("Raw JSON:")
    summary_lines.append("```json")
    summary_lines.append(json.dumps(result, indent=2, sort_keys=True)[:12000])
    summary_lines.append("```")

    return "\n".join(summary_lines)


@tool(
    name="repair_skill_dependencies",
    description=(
        "Repair an already-installed skill by generating requirements from scripts and SKILL.md, "
        "installing into the skill-local .venv, validating required binaries, and optionally running a script smoke test."
    ),
)
def repair_skill_dependencies(
    skill_name: str,
    scope: Literal["user", "shared"] = "user",
    run_scripts: bool = True,
) -> str:
    if _pending_skill_service is None:
        return "Pending skill service not available."
    if _current_user_id is None:
        return "User context not available."

    rep = _pending_skill_service.repair_installed_skill(
        user_id=_current_user_id,
        skill_name=skill_name,
        scope=scope,
        run_scripts=run_scripts,
    )

    if not rep.get("ok"):
        return f"Repair failed: {rep.get('error', 'unknown error')}"

    lines = [
        "Skill repair results:",
        f"- skill: {skill_name}",
        f"- scope: {scope}",
        f"- status: {'OK' if rep.get('ok') else 'FAILED'}",
        "",
        "Raw JSON:",
        "```json",
        json.dumps(rep, indent=2, sort_keys=True)[:12000],
        "```",
    ]
    return "\n".join(lines)
