#!/usr/bin/env python3
"""Kimi for Coding quota collector.

GET https://api.kimi.com/coding/v1/usages with the OAuth bearer omp stores
(Moonshot rotates the refresh token on every refresh, so an expired access
token is refreshed here and the rotated pair written back into omp's
credential database — the same row omp itself maintains).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quotas_common import (  # noqa: E402
    AuthError,
    BROWSER_UA,
    config_secret,
    config_section,
    Unavailable,
    base_record,
    empty_stats,
    http_json,
    limit_entry,
    omp_credential_update,
    omp_credentials,
    parse_iso,
)

AGENT = "kimi"
NAME = "Kimi"
CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
AUTH_BASE = os.environ.get("KIMI_CODE_OAUTH_HOST", "https://auth.kimi.com")
API_BASE = os.environ.get("KIMI_CODE_BASE_URL", "https://api.kimi.com/coding/v1").rstrip("/")

MEMBERSHIP_LABEL = {
    "LEVEL_INTERMEDIATE": "Intermediate",
    "LEVEL_ADVANCED": "Advanced",
    "LEVEL_PRIMARY": "Starter",
}


def device_headers() -> dict[str, str]:
    device_id = ""
    try:
        device_id = (Path.home() / ".omp/agent/kimi-device-id").read_text().strip()
    except OSError:
        pass
    return {
        "User-Agent": "KimiCLI/1.0.48",
        "X-Msh-Platform": "kimi_cli",
        "X-Msh-Version": "1.0.48",
        "X-Msh-Device-Name": "omarchy",
        "X-Msh-Device-Model": "desktop",
        "X-Msh-Os-Version": "Linux",
        "X-Msh-Device-Id": device_id,
    }


def refresh_oauth(cred: dict) -> str:
    """Refresh the access token; persist Moonshot's rotated refresh token."""
    import urllib.parse

    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": cred.get("refresh", ""), "client_id": CLIENT_ID}
    ).encode()
    headers = device_headers() | {"Content-Type": "application/x-www-form-urlencoded"}
    payload = http_json(f"{AUTH_BASE}/api/oauth/token", headers=headers, data=body, method="POST")
    access = str(payload.get("access_token") or "")
    if not access:
        raise AuthError("Kimi token refresh failed — sign in again via omp.")
    cred["access"] = access
    if payload.get("refresh_token"):
        cred["refresh"] = str(payload["refresh_token"])
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        cred["expires"] = int(time.time() * 1000) + int(expires_in) * 1000
    omp_credential_update("kimi-code", cred)
    return access


def access_token(cred: dict) -> str:
    expires = cred.get("expires") or 0
    token = str(cred.get("access") or "")
    if token and expires > time.time() * 1000 + 60000:
        return token
    return refresh_oauth(cred)



WEB_STATS_URL = (
    "https://www.kimi.com/apiv2/kimi.gateway.membership.v2.MembershipService/GetSubscriptionStats"
)


def web_token() -> str:
    """The kimi.com browser token: the kimi-auth cookie value, a full Cookie
    header containing it, or a bare web JWT — from quotas.json / env."""
    raw = (
        config_secret(config_section("kimi"), "token")
        or os.environ.get("KIMI_AUTH_TOKEN", os.environ.get("KIMI_MANUAL_COOKIE", "")).strip()
    )
    if not raw:
        return ""
    match = re.search(r"(?i)kimi-auth=([A-Za-z0-9._\-+=/]+)", raw)
    if match:
        return match.group(1)
    return raw


def web_membership_limits() -> list[dict]:
    token = web_token()
    if not token:
        return []
    try:
        payload = http_json(
            WEB_STATS_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "application/json", "User-Agent": BROWSER_UA},
            data=b"{}",
            method="POST",
        )
    except (Unavailable, AuthError):
        return []
    balance = payload.get("subscriptionBalance") if isinstance(payload.get("subscriptionBalance"), dict) else {}
    ratio = balance.get("amountUsedRatio") or balance.get("kimiCodeUsedRatio")
    try:
        percent = float(ratio)
    except (TypeError, ValueError):
        return []
    if percent <= 1.0:
        percent *= 100.0
    entry = limit_entry("Monthly membership", percent, balance.get("expireTime"), "Monthly")
    return [entry] if entry else []


def scan() -> dict:
    cred = omp_credentials("kimi-code")
    if not cred or not (cred.get("access") or cred.get("refresh")):
        raise AuthError("No Kimi login. Run omp and sign in to kimi-code (omp launch → /auth).", missing=True)

    token = access_token(cred)
    payload = http_json(
        f"{API_BASE}/usages",
        headers=device_headers() | {"Authorization": f"Bearer {token}"},
    )

    record = base_record(AGENT, NAME, ready=True, configured=True)
    record.update(empty_stats())
    level = str(((payload.get("user") or {}).get("membership") or {}).get("level") or "")
    record["tierLabel"] = MEMBERSHIP_LABEL.get(level, "Kimi Coding")

    limits = []

    # The named-window rows: a rolling short window (5h) with its own pool.
    for row in payload.get("limits") or []:
        if not isinstance(row, dict):
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else row
        window = row.get("window") if isinstance(row.get("window"), dict) else {}
        try:
            cap = float(detail.get("limit"))
            remaining = float(detail.get("remaining"))
        except (TypeError, ValueError):
            continue
        if cap <= 0:
            continue
        duration = float(window.get("duration") or 0)
        unit = str(window.get("timeUnit") or "").upper()
        if "MINUTE" in unit and duration:
            label = f"{round(duration / 60)}h window" if duration % 60 == 0 else f"{round(duration)}m window"
            title = "Session"
        else:
            label = "Quota"
            title = ""
        entry = limit_entry(label, (cap - remaining) / cap * 100.0, detail.get("resetTime"), title)
        if entry:
            limits.append(entry)

    # The plan-level pool is the weekly lane (the web API names it
    # ratelimitCode7d; Kimi reports no window metadata here — only the reset).
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    try:
        cap = float(usage.get("limit"))
        used = float(usage.get("used"))
    except (TypeError, ValueError):
        cap = used = 0.0
    if cap > 0:
        entry = limit_entry("Weekly prompts", used / cap * 100.0, usage.get("resetTime"), "Weekly")
        if entry:
            limits.append(entry)

    # The monthly membership lane lives behind the web API: it needs the
    # kimi.com browser token (the kimi-auth cookie), not the coding OAuth
    # token. Optional — without it the tab simply shows 5h + weekly.
    for entry in web_membership_limits():
        limits.append(entry)

    record["limits"] = limits
    if not limits:
        raise Unavailable("Kimi usages response carried no usable windows")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the Kimi quota record as JSON")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limits-only", action="store_true")
    args = parser.parse_args()

    try:
        record = scan()
    except AuthError as error:
        record = base_record(
            AGENT, NAME, configured=not error.missing, usageStatusText="Kimi unavailable", authHelpText=error.message
        )
    except Unavailable as error:
        record = base_record(
            AGENT, NAME, configured=True, retryAdvised=True, usageStatusText="Kimi unreachable",
            authHelpText=str(error),
        )
    except Exception as error:  # noqa: BLE001
        record = base_record(
            AGENT, NAME, configured=True, usageStatusText="Kimi unavailable",
            authHelpText=f"{type(error).__name__}: {error}",
        )
        print(f"quota-kimi: {type(error).__name__}: {error}", file=sys.stderr)
    print(json.dumps(record, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
