#!/usr/bin/env python3
"""Ollama Cloud usage collector.

Subscription usage: GET https://ollama.com/api/usage with an API key in the
Authorization: Bearer header. The payload maps limit buckets (plan-dependent:
session / weekly / monthly on legacy plans, usage credits on newer ones) to
`usage` — a fraction of the applicable cap, not a token count — plus a
4-week activity cost. Buckets are discovered, never hardcoded.

Credential order: quotas.json ollama.apiKey, omp credential
"ollama-cloud", OLLAMA_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quotas_common import (  # noqa: E402
    AuthError,
    base_record,
    config_secret,
    config_section,
    http_json,
    limit_entry,
    number,
    omp_credentials,
)

SETUP = {
    "section": "ollama",
    "guide": [
        "1. Sign in at ollama.com",
        "2. Open Settings → API keys →",
        "   Create new key",
        "3. Paste the key below and save",
    ],
    "inputs": [{"key": "apiKey", "label": "API key", "placeholder": "…"}],
}

USAGE_URL = "https://ollama.com/api/usage"

# Shortest window first: it is the lane most likely to throttle, so it gets
# the binding number; longer buckets arrive as secondary meters.
BUCKET_ORDER = ("session", "weekly", "monthly")
TITLES = {"session": "Session", "weekly": "Weekly", "monthly": "Monthly"}
SECONDARY_AFTER = 1  # only the first (shortest) bucket binds


def credential() -> str:
    key = config_secret(config_section("ollama"), "apiKey", "key")
    if not key:
        cred = omp_credentials("ollama-cloud")
        key = str((cred or {}).get("key") or (cred or {}).get("apiKey") or "").strip()
    if not key:
        key = os.environ.get("OLLAMA_API_KEY", "").strip()
    return key


def scan() -> dict:
    key = credential()
    record = base_record(
        "ollama",
        "Ollama",
        tierLabel="Ollama Cloud",
        configured=bool(key),
        authHelpText="No Ollama key. Sign in to ollama-cloud via omp (omp launch → /auth), or create one at ollama.com/settings/api-keys.",
    )
    if not key:
        raise AuthError(record["authHelpText"], missing=True)

    payload = http_json(USAGE_URL, headers={"Authorization": f"Bearer {key}"})

    buckets = payload.get("limits")
    if not isinstance(buckets, dict) or not buckets:
        raise AuthError("usage endpoint accepted no limit buckets", missing=False)

    def bucket_rank(name: str) -> tuple[int, str]:
        lowered = str(name).lower()
        for index, known in enumerate(BUCKET_ORDER):
            if known in lowered:
                return (index, lowered)
        return (len(BUCKET_ORDER), lowered)

    limits = []
    for position, (name, bucket) in enumerate(
        sorted(buckets.items(), key=lambda item: bucket_rank(item[0]))
    ):
        usage = bucket.get("usage") if isinstance(bucket, dict) else None
        try:
            percent = float(usage) if usage is not None else None
        except (TypeError, ValueError):
            percent = None
        if percent is None:
            continue
        title = TITLES.get(str(name).lower(), str(name).capitalize())
        entry = limit_entry(
            f"{title} usage",
            max(0.0, percent * 100.0),
            "",
            title=title,
        )
        if not entry:
            continue
        if position >= SECONDARY_AFTER:
            entry["secondary"] = True
        limits.append(entry)

    if not limits:
        raise AuthError("usage endpoint reported no usable buckets", missing=False)
    record["limits"] = limits
    record["ready"] = True

    activity = payload.get("activity")
    if isinstance(activity, dict):
        cost = activity.get("cost")
        cost_value = number(cost) if cost is not None else None
        if cost_value is not None:
            record["usageStatusText"] = f"≈ ${cost_value:.2f} usage in the last 4 weeks"

    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limits-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        record = scan()
    except AuthError as error:
        record = base_record(
            "ollama",
            "Ollama",
            tierLabel="Ollama Cloud",
            configured=not getattr(error, "missing", False),
            authHelpText=str(error),
            error=str(error),
            setupSection=SETUP["section"],
            setupGuide=SETUP["guide"],
            setupInputs=SETUP["inputs"],
        )
    except Exception as error:  # noqa: BLE001 - record the failure, keep going
        record = base_record(
            "ollama",
            "Ollama",
            tierLabel="Ollama Cloud",
            configured=True,
            error=f"{type(error).__name__}: {error}",
            setupSection=SETUP["section"],
            setupGuide=SETUP["guide"],
            setupInputs=SETUP["inputs"],
        )

    json.dump(record, sys.stdout, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
