#!/usr/bin/env python3
"""z.ai / Zhipu quota + credit collectors.

Coding-plan quota: GET {origin}/api/monitor/usage/quota/limit with the raw
API key in the Authorization header. TIME_LIMIT is the monthly native-MCP
tool lane (web search, web reader, zread), TOKENS_LIMIT the 5-hour token
window; the legacy Zhipu subscription carries exactly those two — there is
no weekly/monthly token window to invent.

Prepaid credit: GET {origin}/api/biz/account/query-customer-account-report
(the console finance API — it accepts the same API key) returns the exact
ledger: availableBalance / rechargeAmount / giveAmount / totalSpendAmount.

Token windows: GET {origin}/api/monitor/usage/model-usage returns an hourly
token series for the last seven days, folded into day/model buckets.

Providers: zai (api.z.ai, omp credential "zai"), zhipu-cn
(open.bigmodel.cn, omp credential "zhipu-coding-plan" — the coding lane),
zhipu-credit (same credential — the pay-as-you-go credit lane).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quotas_common import (  # noqa: E402
    AuthError,
    Unavailable,
    base_record,
    clamp_percent,
    config_secret,
    config_section,
    empty_stats,
    http_json,
    limit_entry,
    number,
    omp_credentials,
    summarize_zai_model_usage,
    zai_window_label,
)

SETUP = {
    "zhipu-credit": {
        "section": "bigmodel",
        "guide": [
            "Zhipu Credits reads the coding-plan key",
            "from omp by default. To watch a dedicated",
            "pay-as-you-go key instead:",
            "1. Create one at bigmodel.cn/usercenter/",
            "   proj-mgmt/apikeys",
            "2. Paste it below and save",
        ],
        "inputs": [{"key": "apiKey", "label": "PAYG API key", "placeholder": "….…"}],
    },
}

TARGETS = {
    "zai": {
        "origin": "https://api.z.ai",
        "omp_provider": "zai",
        "name": "Z.ai",
        "currency": "USD",
        "lane": "coding",
        "auth_help": "No Z.ai key. Sign in to zai via omp (omp launch → /auth) or set ZAI_API_KEY.",
    },
    "zhipu-cn": {
        "origin": "https://open.bigmodel.cn",
        "omp_provider": "zhipu-coding-plan",
        "name": "Zhipu Coding",
        "currency": "CNY",
        "lane": "coding",
        "auth_help": "No Zhipu coding-plan key. Sign in to zhipu-coding-plan via omp (omp launch → /auth).",
    },
    "zhipu-credit": {
        "origin": "https://open.bigmodel.cn",
        "omp_provider": "zhipu-coding-plan",
        "name": "Zhipu Credits",
        "currency": "CNY",
        "lane": "credit",
        "auth_help": (
            "No Zhipu key. The credit lane reads the same key as the coding plan: "
            "sign in to zhipu-coding-plan via omp, or set bigmodel.apiKey in "
            "~/.config/omarchy/quotas.json."
        ),
    },
}


def credential(agent: str) -> str:
    target = TARGETS[agent]
    cred = omp_credentials(str(target["omp_provider"])) if target["omp_provider"] else None
    key = str((cred or {}).get("key") or "").strip()
    if key:
        return key
    if agent == "zai":
        env = os.environ.get("ZAI_API_KEY", "").strip()
        if env:
            return env
    if agent == "zhipu-credit":
        explicit = config_secret(config_section("bigmodel"), "apiKey") or os.environ.get(
            "ZHIPU_PAYG_API_KEY", os.environ.get("BIGMODEL_API_KEY", "")
        ).strip()
        if explicit:
            return explicit
    raise AuthError(target["auth_help"], missing=True)


def fetch_quota(origin: str, key: str) -> dict:
    quota = http_json(
        f"{origin}/api/monitor/usage/quota/limit",
        headers={"Authorization": key, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    if quota.get("success") is not True or not isinstance(quota.get("data"), dict):
        raise Unavailable(f"quota endpoint rejected the request: {quota.get('msg') or quota.get('code')}")
    return quota["data"]


def fetch_report(origin: str, key: str) -> dict | None:
    try:
        report = http_json(
            f"{origin}/api/biz/account/query-customer-account-report",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
    except (Unavailable, AuthError):
        return None
    return report.get("data") if isinstance(report.get("data"), dict) else None


def fetch_model_usage(origin: str, key: str) -> dict | None:
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d+%H:%M:%S")
    end = datetime.now().strftime("%Y-%m-%d+%H:%M:%S")
    try:
        usage = http_json(
            f"{origin}/api/monitor/usage/model-usage"
            f"?startTime={urllib.parse.quote(start)}&endTime={urllib.parse.quote(end)}",
            headers={"Authorization": key, "User-Agent": "Mozilla/5.0"},
        )
    except (Unavailable, AuthError):
        return None
    return usage.get("data") if isinstance(usage.get("data"), dict) else None


def credit_balance(report: dict, currency: str) -> dict | None:
    remaining = float(report.get("availableBalance") or 0.0)
    funded = float(report.get("rechargeAmount") or 0.0) + float(report.get("giveAmount") or 0.0)
    spent = float(report.get("totalSpendAmount") or 0.0)
    if remaining <= 0 and funded <= 0 and spent <= 0:
        return None
    return {
        "remaining": remaining,
        "funded": funded,
        "spent": spent,
        "currency": currency,
        "estimated": False,
    }


def scan(agent: str) -> dict:
    target = TARGETS[agent]
    origin = target["origin"]
    key = credential(agent)
    record = base_record(agent, target["name"], ready=True, configured=True)
    record.update(empty_stats())

    if target["lane"] == "credit":
        # The pay-as-you-go lane is a ledger, not quota windows: money spent,
        # remaining credit, and the provider's hourly token series.
        report = fetch_report(origin, key)
        if report is None:
            raise Unavailable("Zhipu account report unavailable")
        record["tierLabel"] = "Pay-as-you-go"
        balance = credit_balance(report, target["currency"])
        if balance:
            record["balance"] = balance
        usage = fetch_model_usage(origin, key)
        if usage:
            record.update(summarize_zai_model_usage(usage))
        if not balance and not record["recentDays"]:
            raise Unavailable("Zhipu credit report carried no ledger and no usage")
        return record

    data = fetch_quota(origin, key)
    level = str(data.get("level") or "").strip()
    if agent == "zai" and level:
        record["tierLabel"] = f"Z.ai {level.capitalize()}"
    elif level:
        record["tierLabel"] = level.capitalize() + " plan"
    else:
        record["tierLabel"] = "GLM Coding"

    limits = []
    for row in data.get("limits") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type") or "")
        percent = clamp_percent(row.get("percentage"))
        if percent is None:
            used, cap = number(row.get("currentValue")), number(row.get("usage"))
            percent = (used / cap * 100.0) if cap > 0 else None
        if kind == "TOKENS_LIMIT":
            entry = limit_entry(
                f"{number(row.get('number')) or 5}h window", percent, row.get("nextResetTime"), "Session"
            )
        elif kind == "TIME_LIMIT":
            label, title = zai_window_label(row.get("unit"), row.get("number"))
            entry = limit_entry(label, percent, row.get("nextResetTime"), title)
            # The legacy z.ai/Zhipu subscriptions carry no monthly or weekly
            # token quota — the monthly lane is only the native MCP tool
            # allowance, so it must not outrank the 5-hour token window as
            # the binding number.
            if entry:
                entry["secondary"] = True
        else:
            entry = None
        if entry:
            limits.append(entry)
    record["limits"] = limits

    # Z.ai has no dedicated credit tab, so a funded pay-as-you-go wallet rides
    # its coding tab; Zhipu's ledger belongs to the Zhipu Credits tab alone.
    if agent == "zai":
        report = fetch_report(origin, key)
        if report:
            balance = credit_balance(report, target["currency"])
            if balance:
                record["balance"] = balance
    usage = fetch_model_usage(origin, key)
    if usage:
        record.update(summarize_zai_model_usage(usage))

    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a z.ai-family quota record as JSON")
    parser.add_argument("--agent", default="zai", choices=sorted(TARGETS))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limits-only", action="store_true")
    args = parser.parse_args()
    target = TARGETS[args.agent]

    try:
        record = scan(args.agent)
    except AuthError as error:
        setup = SETUP.get(args.agent)
        record = base_record(args.agent, target["name"], configured=not error.missing,
                             usageStatusText=f"{target['name']} unavailable", authHelpText=error.message,
                             setupSection=setup["section"] if setup else args.agent,
                             setupGuide=setup["guide"] if setup else [],
                             setupInputs=setup["inputs"] if setup else [])
    except Unavailable as error:
        record = base_record(args.agent, target["name"], configured=True, retryAdvised=True,
                             usageStatusText=f"{target['name']} unreachable", authHelpText=str(error))
    except Exception as error:  # noqa: BLE001
        record = base_record(args.agent, target["name"], configured=True,
                             usageStatusText=f"{target['name']} unavailable",
                             authHelpText=f"{type(error).__name__}: {error}")
        print(f"quota-{args.agent}: {type(error).__name__}: {error}", file=sys.stderr)
    print(json.dumps(record, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
