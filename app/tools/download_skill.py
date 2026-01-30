"""Tooling to download skills into pending/.

This is the preferred path for "skill downloads" so that the pending-skill
onboarding pipeline can validate/review/install deps before activation.
"""

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


_skill_service = None
_current_user_id: str | None = None


def set_skill_download_context(skill_service, user_id: str) -> None:
    global _skill_service, _current_user_id
    _skill_service = skill_service
    _current_user_id = user_id


@tool(
    name="download_skill_to_pending",
    description=(
        "Download a skill from a URL into pending/ so it can be AI-reviewed and onboarded later. "
        "scope='user' downloads into skills/<user_id>/pending/<skill>/; scope='shared' downloads into skills/shared/pending/<skill>/"
    ),
)
def download_skill_to_pending(
    url: str,
    scope: Literal["user", "shared"] = "user",
) -> str:
    if _skill_service is None:
        return "Skill service not available."
    if _current_user_id is None:
        return "User context not available."

    try:
        res = _skill_service.download_skill_to_pending(
            url,
            _current_user_id,
            scope=scope,
        )
    except Exception as e:
        return f"Failed to download skill to pending: {e}"

    md = res.get("metadata")
    name = getattr(md, "name", None) if md else None
    installed_at = getattr(md, "installed_at", None) if md else None

    lines = [
        "Skill downloaded to pending:",
        f"- scope: {res.get('scope')}",
        f"- name: {name or '(unknown)'}",
        f"- pending_dir: {res.get('pending_dir')}",
        "",
        "Next:",
        "- Review the pending skill's SKILL.md and scripts.",
        "- Then run onboarding (AI review required): onboard_pending_skills(scope=... , ai_review_completed=true).",
        "",
        "Raw JSON:",
        "```json",
        json.dumps(
            {
                **{k: v for k, v in res.items() if k != "metadata"},
                "metadata": {
                    "name": getattr(md, "name", None),
                    "source_url": getattr(md, "source_url", None),
                    "installed_at": installed_at.isoformat() if installed_at else None,
                }
                if md
                else None,
            },
            indent=2,
            sort_keys=True,
        )[:12000],
        "```",
    ]

    return "\n".join(lines)
