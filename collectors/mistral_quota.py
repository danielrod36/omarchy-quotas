#!/usr/bin/env python3
"""Mistral subscription collector.

Mistral exposes no API-key quota endpoint for La Plateforme plans, so — like
CodexBar — this replays the web console with a pasted session:

  * credits  GET https://admin.mistral.ai/api/billing/credits
             → wallet + credit notes − ongoing usage (prepaid balance)
  * usage    GET https://console.mistral.ai/api-ui/trpc/billing.vibeUsage
             → plan usage percentage + reset (needs csrftoken + ory session)

Credentials: mistral.cookie (full Cookie: header from admin.mistral.ai) and
mistral.csrfToken in ~/.config/omarchy/quotas.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quotas_common import (  # noqa: E402
    BROWSER_UA,
    AuthError,
    Unavailable,
    base_record,
    empty_stats,
    http_json,
    limit_entry,
)

SETUP = {
    "section": "mistral",
    "guide": [
        "1. Log in at admin.mistral.ai",
        "   (the organization console)",
        "2. DevTools (F12) → Network → reload;",
        "   pick any /api/ request",
        "3. Copy its full Cookie request header",
        "   into the first field below",
        "4. Copy the csrftoken cookie value",
        "   into the second field",
    ],
    "inputs": [
        {"key": "cookie", "label": "Cookie header", "placeholder": "ory_session_…; csrftoken=…"},
        {"key": "csrfToken", "label": "csrftoken value", "placeholder": "…"},
    ],
}

AGENT = "mistral"
NAME = "Mistral"
ADMIN = "https://admin.mistral.ai"
CONSOLE = "https://console.mistral.ai"

VIBE_INPUT = urllib.parse.quote('{"0":{"json":null,"meta":{"values":["undefined"],"v":1}}}', safe="")


def clean_csrf(raw: str) -> str:
    token = str(raw or "").strip()
    if not token or any(c in token for c in ";,\r\n"):
        raise AuthError("Mistral csrfToken is missing or malformed in quotas.json.")
    return token


def console_cookie(cookie: str, csrf: str) -> str:
    pairs = [f"csrftoken={csrf}"]
    for part in cookie.split(";"):
        name = part.split("=", 1)[0].strip()
        if name.startswith("ory_session_"):
            pairs.append(part.strip())
    return "; ".join(pairs)


def fetch_credits(cookie: str, csrf: str) -> dict:
    headers = {
        "Accept": "*/*",
        "Cookie": cookie,
        "Referer": f"{ADMIN}/organization/billing",
        "Origin": ADMIN,
        "User-Agent": BROWSER_UA,
    }
    if csrf:
        headers["X-CSRFTOKEN"] = csrf
    return http_json(f"{ADMIN}/api/billing/credits", headers=headers)


def fetch_vibe(cookie: str, csrf: str) -> dict | None:
    try:
        payload = http_json(
            f"{CONSOLE}/api-ui/trpc/billing.vibeUsage?batch=1&input={VIBE_INPUT}",
            headers={
                "Accept": "*/*",
                "Cookie": console_cookie(cookie, csrf),
                "X-CSRFToken": csrf,
                "Referer": f"{CONSOLE}/",
                "Origin": CONSOLE,
                "User-Agent": BROWSER_UA,
            },
        )
    except (Unavailable, AuthError):
        return None
    # tRPC batch: [{result: {data: {json: {...}}}}]
    try:
        rows = payload.get("result") if isinstance(payload.get("result"), list) else [payload]
        for row in rows or []:
            data = row.get("result", {}).get("data", {})
            js = data.get("json") if isinstance(data, dict) else None
            if isinstance(js, dict):
                return js
    except (AttributeError, TypeError):
        pass
    return None


def scan() -> dict:
    from quotas_common import config_secret, config_section

    section = config_section("mistral")
    cookie = config_secret(section, "cookie")
    if not cookie:
        raise AuthError(
            "No Mistral console session. Log in at admin.mistral.ai, copy the request's "
            "Cookie header, and set mistral.cookie (plus mistral.csrfToken from the "
            "csrftoken cookie) in ~/.config/omarchy/quotas.json.",
            missing=True,
        )
    csrf = clean_csrf(config_secret(section, "csrfToken") or _csrf_from_cookie(cookie))

    record = base_record(AGENT, NAME, ready=True, configured=True)
    record.update(empty_stats())
    record["tierLabel"] = "Pro"

    credits = fetch_credits(cookie, csrf)
    wallet = float(credits.get("walletAmount") or 0.0)
    notes = float(credits.get("creditNotesAmount") or 0.0)
    ongoing = float(credits.get("ongoingUsageBalance") or 0.0)
    currency = str(credits.get("currency") or "EUR")
    record["balance"] = {
        "remaining": max(0.0, wallet + notes - ongoing),
        "funded": wallet + notes,
        "spent": ongoing,
        "currency": currency,
        "estimated": False,
    }

    vibe = fetch_vibe(cookie, csrf)
    if vibe:
        percent = vibe.get("usagePercentage")
        if isinstance(percent, (int, float)) and 0 <= percent <= 100:
            entry = limit_entry("Monthly usage", percent, vibe.get("resetAt"), "Monthly")
            if entry:
                record["limits"] = [entry]

    return record


def _csrf_from_cookie(cookie: str) -> str:
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "csrftoken":
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the Mistral record as JSON")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limits-only", action="store_true")
    args = parser.parse_args()

    try:
        record = scan()
    except AuthError as error:
        record = base_record(AGENT, NAME, configured=not error.missing, usageStatusText="Mistral unavailable",
                             authHelpText=error.message, setupSection=SETUP["section"],
                             setupGuide=SETUP["guide"], setupInputs=SETUP["inputs"])
    except Unavailable as error:
        record = base_record(AGENT, NAME, configured=True, retryAdvised=True, usageStatusText="Mistral unreachable",
                             authHelpText=str(error))
    except Exception as error:  # noqa: BLE001
        record = base_record(AGENT, NAME, configured=True, usageStatusText="Mistral unavailable",
                             authHelpText=f"{type(error).__name__}: {error}")
        print(f"quota-mistral: {type(error).__name__}: {error}", file=sys.stderr)
    print(json.dumps(record, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
