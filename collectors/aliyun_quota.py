#!/usr/bin/env python3
"""Aliyun (Bailian) Token Plan quota collector.

Aliyun's Token Plan has no API-key usage endpoint; the numbers come from the
Bailian console gateway, replaying the browser session omp already stores
(the DevTools `Cookie:` header captured during omp's alibaba-token-plan
login): a session page yields SEC_TOKEN, then the gateway POST returns the
5-hour and 7-day credit percentages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quotas_common import (  # noqa: E402
    BROWSER_UA,
    AuthError,
    Unavailable,
    base_record,
    clamp_percent,
    empty_stats,
    http_json,
    limit_entry,
    omp_credentials,
    parse_iso,
)

SETUP = {
    "section": "aliyun",
    "guide": [
        "The Bailian console cookie is captured",
        "through omp, not pasted here:",
        "1. Run omp, open /auth, pick",
        "   alibaba-token-plan",
        "2. When omp asks for the quota capture:",
        "   reload the Token Plan page and pick the",
        "   bailian-cs.console.aliyun.com",
        "   data/api.json request whose api query",
        "   ends in /tokenplan/personal/api/v2/usage",
        "3. Copy its complete Cookie header",
        "   into omp's prompt",
        "4. The tab reappears on next refresh",
    ],
    "inputs": [],
}

AGENT = "aliyun"
NAME = "Aliyun"
ORIGIN = "https://bailian.console.aliyun.com"
SESSION_URL = f"{ORIGIN}/cn-beijing?tab=plan"
USAGE_API = "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage"
USAGE_URL = (
    "https://bailian-cs.console.aliyun.com/data/api.json"
    f"?action=BroadScopeAspnGateway&product=sfm_bailian&api={urllib.parse.quote(USAGE_API, safe='')}"
)


def cookie_value(cookie: str, name: str) -> str:
    for part in cookie.split(";"):
        if "=" in part and part.split("=", 1)[0].strip() == name:
            return part.split("=", 1)[1].strip()
    return ""


def stored_cookie() -> str:
    cred = omp_credentials("alibaba-token-plan")
    if not cred:
        raise AuthError("No Aliyun Token Plan login. Add it in omp (omp launch → /auth).", missing=True)
    try:
        parsed = json.loads(str(cred.get("key") or "{}"))
    except json.JSONDecodeError:
        parsed = {}
    cookie = str(parsed.get("cookie") or "").strip()
    if not cookie:
        raise AuthError("omp's alibaba-token-plan credential carries no console cookie — re-login in omp.", missing=True)
    return cookie


def scan() -> dict:
    cookie = stored_cookie()

    # The session page is HTML: fetch it raw to lift the per-session SEC_TOKEN.
    sec_token = ""
    try:
        import urllib.request

        request = urllib.request.Request(SESSION_URL, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Cookie": cookie,
            "Referer": f"{ORIGIN}/",
            "User-Agent": BROWSER_UA,
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", "replace")
        match = re.search(r'\bSEC_TOKEN\s*:\s*"([^"]+)"', html)
        if match:
            sec_token = match.group(1)
    except Exception as error:  # noqa: BLE001
        raise Unavailable(f"Bailian session page fetch failed: {error}") from error
    if not sec_token:
        raise AuthError(
            "Bailian console cookie expired (no SEC_TOKEN in session page). "
            "Re-capture it: omp → /auth → alibaba-token-plan, paste a fresh Cookie header.",
            missing=True,
        )

    csrf = cookie_value(cookie, "login_aliyunid_csrf") or cookie_value(cookie, "csrf")
    params = json.dumps({
        "Api": USAGE_API,
        "Data": {"cornerstoneParam": {
            "feTraceId": str(uuid.uuid4()),
            "feURL": f"{ORIGIN}/cn-beijing?tab=plan#/efm/subscription/token-plan/personal",
            "protocol": "V2", "console": "ONE_CONSOLE", "productCode": "p_efm",
            "switchAgent": 12608464, "switchUserType": 3,
            "domain": "bailian.console.aliyun.com", "consoleSite": "BAILIAN_ALIYUN",
            "userNickName": "", "userPrincipalName": "", "xsp_lang": "zh-CN",
        }},
        "V": "1.0",
    })
    body = urllib.parse.urlencode({
        "product": "sfm_bailian", "action": "BroadScopeAspnGateway",
        "region": "cn-beijing", "sec_token": sec_token, "params": params,
    }).encode()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/cn-beijing?tab=plan",
        "User-Agent": BROWSER_UA,
        "X-Requested-With": "XMLHttpRequest",
    }
    if csrf:
        headers["x-xsrf-token"] = csrf
        headers["x-csrf-token"] = csrf

    payload = http_json(USAGE_URL, headers=headers, data=body, method="POST")
    if payload.get("successResponse") is False:
        raise AuthError("Bailian gateway rejected the request — the console cookie is likely stale.", missing=True)

    data = payload.get("data") or {}
    if isinstance(data.get("Data"), str):
        try:
            data = json.loads(data["Data"])
        except json.JSONDecodeError:
            pass
    if isinstance(data.get("DataV2"), dict) and isinstance(data["DataV2"].get("data"), dict):
        data = data["DataV2"]["data"]
    if isinstance(data.get("data"), dict):
        data = data["data"]

    limits = []
    window_5h = clamp_percent(data.get("per5HourPercentage"))
    if window_5h is not None:
        limits.append(limit_entry("5h window", window_5h, data.get("per5HourResetTime"), "Session"))
    week = clamp_percent(data.get("per1WeekPercentage"))
    if week is not None:
        limits.append(limit_entry("Weekly credits", week, data.get("per1WeekResetTime"), "Weekly"))
    if not limits:
        raise Unavailable("Bailian usage response carried no percentages")

    record = base_record(AGENT, NAME, ready=True, configured=True)
    record.update(empty_stats())
    record["tierLabel"] = "Token Plan"
    record["limits"] = limits
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the Aliyun Token Plan quota record as JSON")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limits-only", action="store_true")
    args = parser.parse_args()

    try:
        record = scan()
    except AuthError as error:
        record = base_record(AGENT, NAME, configured=not error.missing, usageStatusText="Aliyun unavailable",
                             authHelpText=error.message, setupSection=SETUP["section"],
                             setupGuide=SETUP["guide"], setupInputs=SETUP["inputs"])
    except Unavailable as error:
        record = base_record(AGENT, NAME, configured=True, retryAdvised=True, usageStatusText="Aliyun unreachable",
                             authHelpText=str(error))
    except Exception as error:  # noqa: BLE001
        record = base_record(AGENT, NAME, configured=True, usageStatusText="Aliyun unavailable",
                             authHelpText=f"{type(error).__name__}: {error}")
        print(f"quota-aliyun: {type(error).__name__}: {error}", file=sys.stderr)
    print(json.dumps(record, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
