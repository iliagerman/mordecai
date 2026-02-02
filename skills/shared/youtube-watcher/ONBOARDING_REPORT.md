# Pending Skill Preflight Report: youtube-watcher

- Scope: shared
- User: (n/a)
- Timestamp: 2026-02-02T11:53:10.062048+00:00
- Status: FAILED

## normalize_skill_md (OK)

```json
{
  "actions": [],
  "ok": true,
  "skill_md": "/app/skills/shared/pending/youtube-watcher/SKILL.md"
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
  "generated": false,
  "ok": true,
  "reason": "no third-party imports detected",
  "warnings": []
}
```

## sync_requires_pip (OK)

```json
{
  "ok": true,
  "reason": "no requirements.txt",
  "updated": false
}
```

## validate_python_syntax (OK)

```json
{
  "checked": 1,
  "ok": true
}
```

## install_dependencies (OK)

```json
{
  "installed": false,
  "ok": true,
  "reason": "no requirements.txt"
}
```

## validate_required_bins (OK)

```json
{
  "checked": 0,
  "ok": true,
  "reason": "no required bins declared"
}
```

## run_scripts_smoke_test (FAILED)

```json
{
  "failures": [
    {
      "missing_module": null,
      "returncode": 2,
      "script": "scripts/get_transcript.py",
      "stderr": "usage: get_transcript.py [-h] url\nget_transcript.py: error: the following arguments are required: url\n",
      "stdout": ""
    }
  ],
  "missing_modules": [],
  "ok": false,
  "python": "/app/.venv/bin/python3",
  "ran": 1,
  "timeout_seconds": 20
}
```
