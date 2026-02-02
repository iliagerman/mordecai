# Pending Skill Preflight Report: local-places

- Scope: user
- User: splintermaster
- Timestamp: 2026-02-02T15:53:10.168132+00:00
- Status: FAILED

## normalize_skill_md (OK)

```json
{
  "actions": [],
  "ok": true,
  "skill_md": "/app/skills/splintermaster/pending/local-places/SKILL.md"
}
```

## validate_required_env (OK)

```json
{
  "checked": 0,
  "ok": true,
  "reason": "no required env declared"
}
```

## validate_required_config_files (OK)

```json
{
  "checked": 0,
  "ok": true,
  "reason": "no config templates found"
}
```

## generate_requirements (OK)

```json
{
  "added": [
    "local_places"
  ],
  "created": false,
  "declared_from_skill_md": [],
  "generated": true,
  "ok": true,
  "path": "/app/skills/splintermaster/pending/local-places/requirements.txt",
  "requirements": [
    "fastapi",
    "httpx",
    "local_places",
    "pydantic",
    "uvicorn"
  ],
  "warnings": []
}
```

## sync_requires_pip (OK)

```json
{
  "ok": true,
  "reason": "already in sync",
  "updated": false
}
```

## validate_python_syntax (OK)

```json
{
  "checked": 6,
  "ok": true
}
```

## install_dependencies (FAILED)

```json
{
  "error": "pip install failed",
  "ok": false,
  "stderr": "  \u00d7 No solution found when resolving dependencies:\n  \u2570\u2500\u25b6 Because local-places was not found in the package registry and you\n      require local-places, we can conclude that your requirements are\n      unsatisfiable.\n",
  "stdout": ""
}
```
