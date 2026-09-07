#!/usr/bin/env python3
"""Balance collectors for prepaid API providers.

deepseek   GET https://api.deepseek.com/user/balance      (omp key or DEEPSEEK_API_KEY)
openrouter GET https://openrouter.ai/api/v1/credits       (management key)
mimo       GET https://platform.xiaomimimo.com/api/v1/balance (console cookie)
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
    cached_peak,
    config_secret,
    config_section,
    empty_stats,
    http_json,
    omp_credentials,
)

SETUP = {
    "openrouter": {
        "section": "openrouter",
        "guide": [
            "1. Open openrouter.ai/settings/",
            "   management-keys",
            "2. Create a Management key —",
            "   plain API keys get 403 on /credits",
            "3. Paste the sk-or-v1-… key below",
        ],
        "inputs": [{"key": "apiKey", "label": "Management key", "placeholder": "sk-or-v1-…"}],
    },
    "mimo": {
        "section": "mimo",
        "guide": [
            "1. Log in at platform.xiaomimimo.com",
            "   and open the Balance page",
            "2. DevTools (F12) → Network → reload",
            "3. Pick any request to the platform,",
            "   copy its full Cookie request header",
            "· must contain api-platform_serviceToken",
            "  and userId cookies",
            "4. Paste the whole header below",
        ],
        "inputs": [{"key": "cookie", "label": "Cookie header", "placeholder": "api-platform_serviceToken=…; userId=…"}],
    },
    "deepseek": {
        "section": "deepseek",
        "guide": [
            "1. Create a key at",
            "   platform.deepseek.com/api_keys",
            "2. Paste it below (or sign in via omp)",
        ],
        "inputs": [{"key": "apiKey", "label": "API key", "placeholder": "sk-…"}],
    },
}

SPECS = {
    "deepseek": {"name": "DeepSeek", "tier": "Pay-as-you-go", "currency": "CNY"},
    "openrouter": {"name": "OpenRouter", "tier": "Prepaid credits", "currency": "USD"},
    "mimo": {"name": "MiMo", "tier": "Prepaid", "currency": "CNY"},
}


def scan_deepseek() -> dict:
    cred = omp_credentials("deepseek")
    key = str((cred or {}).get("key") or "").strip() or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        key = config_secret(config_section("deepseek"), "apiKey")
    if not key:
        raise AuthError("No DeepSeek key. Sign in via omp or set DEEPSEEK_API_KEY.", missing=True)
    payload = http_json(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    rows = payload.get("balance_infos") or []
    row = next((r for r in rows if str(r.get("currency", "")).upper() == "CNY"), next(iter(rows), None))
    if not isinstance(row, dict):
        raise Unavailable("DeepSeek balance response carried no balance_infos")
    currency = str(row.get("currency") or "CNY")
    total = float(row.get("total_balance") or 0.0)
    funded = cached_peak("deepseek", total)
    record = base_record("deepseek", "DeepSeek", ready=True, configured=True)
    record.update(empty_stats())
    record["tierLabel"] = "Pay-as-you-go"
    record["balance"] = {
        "remaining": total,
        "funded": funded,
        "spent": max(0.0, funded - total),
        "currency": currency,
        "estimated": False,
    }
    return record


def scan_openrouter() -> dict:
    key = (
        config_secret(config_section("openrouter"), "apiKey")
        or os.environ.get("OPENROUTER_API_KEY", "").strip()
    )
    if not key:
        raise AuthError(
            "No OpenRouter key. Set openrouter.apiKey in ~/.config/omarchy/quotas.json "
            "or OPENROUTER_API_KEY — must be a Management key (openrouter.ai/settings/management-keys).",
            missing=True,
        )
    payload = http_json(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise Unavailable("OpenRouter credits response carried no data")
    credits = float(data.get("total_credits") or 0.0)
    used = float(data.get("total_usage") or 0.0)
    record = base_record("openrouter", "OpenRouter", ready=True, configured=True)
    record.update(empty_stats())
    record["tierLabel"] = "Prepaid credits"
    record["balance"] = {
        "remaining": max(0.0, credits - used),
        "funded": credits,
        "spent": used,
        "currency": "USD",
        "estimated": False,
    }
    return record


def scan_mimo() -> dict:
    section = config_section("mimo")
    cookie = config_secret(section, "cookie")
    if not cookie:
        raise AuthError(
            "No MiMo session. MiMo exposes no balance API key — log in at "
            "platform.xiaomimimo.com/#/console/balance, copy the request's Cookie header "
            "(needs api-platform_serviceToken and userId), and set mimo.cookie in "
            "~/.config/omarchy/quotas.json.",
            missing=True,
        )
    if "api-platform_serviceToken" not in cookie or "userId" not in cookie:
        raise AuthError("MiMo cookie is missing api-platform_serviceToken / userId — recopy the full Cookie header.")
    base = str(section.get("apiUrl") or "https://platform.xiaomimimo.com/api/v1").rstrip("/")
    payload = http_json(
        f"{base}/balance",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://platform.xiaomimimo.com",
            "Referer": "https://platform.xiaomimimo.com/#/console/balance",
            "User-Agent": BROWSER_UA,
        },
    )
    code = payload.get("code")
    if code == 401:
        raise AuthError("MiMo browser session expired — log in again and recopy the cookie.")
    if code == 403:
        raise AuthError("MiMo rejected the session cookie.")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if code != 0 or data is None:
        raise Unavailable(f"MiMo balance response invalid (code {code})")
    total = float(data.get("balance") or 0.0)
    currency = str(data.get("currency") or "CNY")
    funded = cached_peak("mimo", total)
    record = base_record("mimo", "MiMo", ready=True, configured=True)
    record.update(empty_stats())
    record["tierLabel"] = "Prepaid"
    record["balance"] = {
        "remaining": total,
        "funded": funded,
        "spent": max(0.0, funded - total),
        "currency": currency,
        "estimated": False,
    }
    return record


SCANNERS = {"deepseek": scan_deepseek, "openrouter": scan_openrouter, "mimo": scan_mimo}


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a prepaid-provider balance record as JSON")
    parser.add_argument("--agent", required=True, choices=sorted(SCANNERS))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limits-only", action="store_true")
    args = parser.parse_args()
    spec = SPECS[args.agent]

    try:
        record = SCANNERS[args.agent]()
    except AuthError as error:
        setup = SETUP.get(args.agent, {})
        record = base_record(args.agent, spec["name"], configured=not error.missing,
                             usageStatusText=f"{spec['name']} unavailable", authHelpText=error.message,
                             setupSection=setup.get("section", args.agent),
                             setupGuide=setup.get("guide", []),
                             setupInputs=setup.get("inputs", []))
    except Unavailable as error:
        record = base_record(args.agent, spec["name"], configured=True, retryAdvised=True,
                             usageStatusText=f"{spec['name']} unreachable", authHelpText=str(error))
    except Exception as error:  # noqa: BLE001
        record = base_record(args.agent, spec["name"], configured=True,
                             usageStatusText=f"{spec['name']} unavailable",
                             authHelpText=f"{type(error).__name__}: {error}")
        print(f"quota-{args.agent}: {type(error).__name__}: {error}", file=sys.stderr)
    print(json.dumps(record, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
