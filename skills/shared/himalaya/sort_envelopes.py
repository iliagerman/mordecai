#!/usr/bin/env python3
"""Client-side sorting helper for Himalaya envelope JSON.

Why this exists:
- Some IMAP providers can hang/stall when asked to do server-side sorting
  (e.g. query contains `order by date desc`).
- In automated runs we prefer: bounded fetch + client-side sort.

Usage:
  himalaya envelope list --output json --page 1 --page-size 50 \
    | python3 skills/shared/himalaya/sort_envelopes.py --desc \
    > envelopes.sorted.json

Notes:
- The script tries common date fields: "date", "internal_date", "received_at".
- It outputs JSON to stdout (same structure as input: list or object containing a list).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable


@dataclass(frozen=True)
class SortOptions:
    descending: bool
    date_fields: tuple[str, ...]


def _parse_args(argv: list[str]) -> SortOptions:
    parser = argparse.ArgumentParser(
        description="Sort Himalaya envelope JSON client-side (avoids IMAP server-side sort hangs)."
    )
    parser.add_argument(
        "--desc",
        action="store_true",
        help="Sort newest-first (descending). Default is ascending.",
    )
    parser.add_argument(
        "--date-field",
        action="append",
        default=[],
        help=(
            "Date field to consider (can be repeated). "
            "Defaults to: date, internal_date, received_at."
        ),
    )
    ns = parser.parse_args(argv)

    date_fields = (
        tuple(ns.date_field) if ns.date_field else ("date", "internal_date", "received_at")
    )
    return SortOptions(descending=bool(ns.desc), date_fields=date_fields)


def _coerce_datetime(value: object) -> datetime | None:
    """Best-effort parse into an aware UTC datetime.

    Supports:
    - RFC 2822-ish strings (common in email headers)
    - ISO-8601-ish strings (best effort)
    - numeric unix timestamps (seconds)
    """

    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None

    if not isinstance(value, str):
        return None

    s = value.strip()
    if not s:
        return None

    # RFC 2822 / email date formats
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # ISO-8601 best effort (handle trailing Z)
    try:
        iso = s.replace("Z", "+00:00")
        dt2 = datetime.fromisoformat(iso)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return dt2.astimezone(timezone.utc)
    except Exception:
        return None


def _extract_envelopes(payload: object) -> tuple[list[dict], str | None, object]:
    """Returns (envelopes, container_key, container_obj).

    - If payload is a list: container_key=None and container_obj is the list.
    - If payload is a dict and contains a list under a common key, container_key is that key.

    This lets us preserve the original shape on output.
    """

    if isinstance(payload, list):
        # Expect list[dict]
        envelopes: list[dict] = [e for e in payload if isinstance(e, dict)]
        return envelopes, None, payload

    if isinstance(payload, dict):
        # Try common keys; fall back to the first list value.
        for key in ("envelopes", "items", "data", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                envelopes = [e for e in val if isinstance(e, dict)]
                return envelopes, key, payload

        for key, val in payload.items():
            if isinstance(val, list):
                envelopes = [e for e in val if isinstance(e, dict)]
                return envelopes, key, payload

    raise ValueError("Unsupported JSON shape: expected list or object containing a list")


def _date_sort_key(
    envelope: dict,
    date_fields: Iterable[str],
    *,
    descending: bool,
) -> tuple[int, float, str]:
    """Return a total ordering key.

    Important: undated envelopes should always appear last, even when sorting
    descending (newest-first). Therefore we do NOT use reverse=True on the full
    tuple; instead we negate the timestamp when descending.

    Key parts:
    - missing_flag: 0=has date, 1=missing (so dated items always come first)
    - sort_value: timestamp (or negative timestamp for descending)
    - tiebreaker: stable string to avoid output jitter
    """

    dt: datetime | None = None
    used_field = ""
    for f in date_fields:
        if f in envelope:
            dt = _coerce_datetime(envelope.get(f))
            used_field = f
            if dt is not None:
                break

    if dt is None:
        # Push undated to the end.
        tiebreaker = json.dumps(envelope, sort_keys=True, ensure_ascii=False)
        return (1, 0.0, tiebreaker)

    ts = dt.timestamp()
    sort_value = -ts if descending else ts
    # Use field name as part of stable tie-breaker so output doesn't jitter across runs.
    return (0, sort_value, used_field)


def main(argv: list[str]) -> int:
    opts = _parse_args(argv)

    raw = sys.stdin.read()
    if not raw.strip():
        print("Expected JSON on stdin", file=sys.stderr)
        return 2

    payload = json.loads(raw)
    envelopes, container_key, container_obj = _extract_envelopes(payload)

    envelopes_sorted = sorted(
        envelopes,
        key=lambda e: _date_sort_key(e, opts.date_fields, descending=opts.descending),
    )

    # Preserve original JSON shape
    if container_key is None:
        out_obj = envelopes_sorted
    else:
        if not isinstance(container_obj, dict):
            raise AssertionError("container_obj must be dict when container_key is set")
        out_obj = dict(container_obj)
        out_obj[container_key] = envelopes_sorted

    json.dump(out_obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
