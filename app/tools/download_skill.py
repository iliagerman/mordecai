"""Tooling to download skills into pending/.

This is the preferred path for "skill downloads" so that the pending-skill
onboarding pipeline can validate/review/install deps before activation.

Skills installed via this tool (when a user asks the bot to install) are always
installed to the user's personal folder, never to the shared folder.
"""

from __future__ import annotations

import json

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
        "Download a skill from a URL into the user's personal pending/ folder "
        "so it can be AI-reviewed and onboarded later. Skills are always installed "
        "to the user's personal folder (skills/<user_id>/pending/<skill>/) and never "
        "to the shared folder."
    ),
)
def download_skill_to_pending(
    url: str,
) -> str:
    """Download a skill to the user's personal pending folder.

    This tool is used by agents when a user asks to install a skill.
    Skills are always installed to the user's personal folder, never to shared.

    Args:
        url: The URL to download the skill from.

    Returns:
        A message describing the result of the download.
    """
    if _skill_service is None:
        return "Skill service not available."
    if _current_user_id is None:
        return "User context not available."

    try:
        # Always install to user's personal folder, never to shared
        res = _skill_service.download_skill_to_pending(
            url,
            _current_user_id,
            scope="user",
        )
    except Exception as e:
        return f"Failed to download skill to pending: {e}"

    md = res.get("metadata")
    name = getattr(md, "name", None) if md else None
    installed_at = getattr(md, "installed_at", None) if md else None

    lines = [
        "Skill downloaded to your personal pending folder:",
        f"- name: {name or '(unknown)'}",
        f"- pending_dir: {res.get('pending_dir')}",
        "",
        "Next:",
        "- Review the pending skill's SKILL.md and scripts.",
        "- Then run onboarding (AI review required): onboard_pending_skills(ai_review_completed=true).",
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
