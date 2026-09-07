#!/usr/bin/env python3
"""Shared plumbing for the daniel.quotas collectors.

Every collector prints one display-ready JSON record matching the contract
the Omarchy agents panel renders (see omarchy-agent-usage-fireworks):
limits[], balance{}, token stats, tierLabel, usageStatusText, authHelpText.

Credentials come from omp (~/.omp/agent/agent.db, which omp keeps fresh —
including OAuth token refreshes) and from ~/.config/omarchy/quotas.json for
providers omp does not store.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OMP_DB = Path(os.environ.get("OMP_AGENT_DB") or (Path.home() / ".omp/agent/agent.db"))
CONFIG_PATH = Path(
    os.environ.get("QUOTAS_CONFIG")
    or (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "omarchy" / "quotas.json")
)
CACHE_DIR = Path(
    os.environ.get("QUOTAS_CACHE")
    or (Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "omarchy" / "quotas")
)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


class Unavailable(Exception):
    """A provider endpoint could not be reached at all — advises a retry."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthError(Exception):
    """Credentials are missing, expired, or rejected.

    `missing=True` means no credential exists at all — the panel hides the
    provider entirely. A stored-but-rejected credential keeps its tab so the
    error card stays readable.
    """

    def __init__(self, message: str, missing: bool = False):
        super().__init__(message)
        self.message = message
        self.missing = missing


# ------------------------------------------------------------------ records


def base_record(agent_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schemaVersion": 1,
        "id": agent_id,
        "name": name,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "ready": False,
        # API-reported numbers are account-global, not machine-local.
        "scope": "account",
        "hasLocalStats": False,
        "hasPromptStats": False,
        "tierLabel": "",
        "usageStatusText": "",
        "authHelpText": "",
        "limits": [],
        # True when this machine carries an explicit credential/config entry
        # for the provider: the panel then keeps the tab (and its error card)
        # visible even while the endpoint is unreachable.
        "configured": False,
    }
    record.update(overrides)
    return record


def limit_entry(label: str, percent: Any, resets_at: Any = "", title: str = "") -> dict[str, Any] | None:
    value = float(percent) if percent is not None else None
    if value is None or not (value >= 0):
        return None
    reset = ""
    if resets_at:
        if isinstance(resets_at, (int, float)):
            ms = float(resets_at)
            reset = datetime.fromtimestamp(ms / 1000.0, timezone.utc).isoformat()
        else:
            reset = str(resets_at)
    entry = {"label": str(label), "percent": min(value, 100.0) / 100.0, "resetsAt": reset}
    if title:
        entry["title"] = str(title)
    return entry


def balance_entry(
    remaining: float, funded: float, spent: float, currency: str, estimated: bool = False
) -> dict[str, Any]:
    return {
        "remaining": max(0.0, float(remaining)),
        "funded": max(0.0, float(funded)),
        "spent": max(0.0, float(spent)),
        "currency": str(currency or "USD"),
        "estimated": bool(estimated),
    }


def empty_stats() -> dict[str, Any]:
    return {
        "todayPrompts": 0,
        "todaySessions": 0,
        "todayTotalTokens": 0,
        "todayTokensByModel": {},
        "recentDays": [],
        "totalPrompts": 0,
        "totalSessions": 0,
        "activeDays": 0,
        "activeDates": [],
        "modelUsage": {},
    }


# ------------------------------------------------------------------ helpers


def number(value: Any) -> int:
    try:
        return max(0, round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def clamp_percent(value: Any) -> float | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if not (raw >= 0):
        return None
    # Providers disagree on 0-1 vs 0-100; anything above 1 with a ceiling of
    # 100 reads as percent.
    if raw <= 1.0 and raw > 0.0:
        return raw * 100.0 if raw < 1.0 else 100.0
    return min(raw, 100.0)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def http_json(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        code = error.code
        detail = error.read()[:200].decode("utf-8", "replace")
        if code in (401, 403):
            # A rejected credential is as good as none: the panel hides the
            # provider and offers its setup entry instead.
            raise AuthError(f"endpoint returned HTTP {code}: {detail}", missing=True) from error
        raise Unavailable(f"endpoint returned HTTP {code}: {detail}") from error
    except urllib.error.URLError as error:
        raise Unavailable(f"could not reach the endpoint ({error.reason})") from error
    except TimeoutError as error:
        raise Unavailable("request timed out") from error
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise Unavailable("response was not valid JSON") from error
    return parsed if isinstance(parsed, dict) else {}


# ------------------------------------------------------- credential sources


def load_config() -> dict[str, Any]:
    try:
        parsed = json.loads(CONFIG_PATH.read_text())
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def config_section(name: str) -> dict[str, Any]:
    section = load_config().get(name)
    return section if isinstance(section, dict) else {}


def config_secret(section: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(section.get(key) or "").strip()
        if value:
            return value
    return ""


def omp_credentials(provider: str) -> dict[str, Any] | None:
    """Read omp's stored credential for a provider (omp keeps tokens fresh)."""
    if not OMP_DB.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{OMP_DB}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT data FROM auth_credentials WHERE provider = ? AND disabled_cause IS NULL",
                (provider,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        parsed = json.loads(row[0])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def omp_credential_update(provider: str, data: dict[str, Any]) -> bool:
    """Persist a rotated credential back into omp's database.

    Kimi rotates its refresh token on every use, so a collector that refreshes
    externally must write the new pair back or omp's next refresh would use a
    token that has already been consumed.
    """
    try:
        conn = sqlite3.connect(str(OMP_DB), timeout=5)
        try:
            conn.execute(
                "UPDATE auth_credentials SET data = ?, updated_at = CAST(strftime('%s','now') AS INTEGER) "
                "WHERE provider = ?",
                (json.dumps(data), provider),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


# ------------------------------------------------------------------ caching


def cached_peak(name: str, value: float) -> float:
    """Track the high-water mark for providers that report only a remaining
    balance (DeepSeek, MiMo): the peak observed plays "funded" so the meter
    drains as the account is spent down."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}-peak.json"
    peak = float(value)
    try:
        previous = json.loads(path.read_text())
        if isinstance(previous, (int, float)) and previous > peak:
            peak = float(previous)
    except (OSError, json.JSONDecodeError):
        pass
    try:
        path.write_text(json.dumps(peak))
    except OSError:
        pass
    return peak


# ------------------------------------------------------------ z.ai analytics

def zai_window_label(unit: Any, count: Any) -> tuple[str, str]:
    """(label, title) for a z.ai-style window unit enum."""
    unit_id = number(unit)
    n = max(1, number(count))
    if unit_id == 3:
        return f"{n}h window", "Session"
    if unit_id == 4:
        return f"{n}d requests", f"{n}-day"
    if unit_id == 6:
        return "Weekly requests", "Weekly"
    if unit_id == 5:
        return "Monthly tools", "Monthly"
    return "Quota", "Quota"


def summarize_zai_model_usage(payload: dict[str, Any]) -> dict[str, Any]:
    """Fold the hourly model-usage series into day buckets + model totals."""
    stamps = [str(s) for s in (payload.get("x_time") or [])]
    today = date.today()
    days = {(today - timedelta(days=offset)).isoformat(): 0 for offset in range(6, -1, -1)}
    totals: dict[str, int] = {}

    for model_row in payload.get("modelDataList") or []:
        if not isinstance(model_row, dict):
            continue
        name = str(model_row.get("modelName") or "unknown")
        series = model_row.get("tokensUsage") or []
        total = 0
        for i, raw in enumerate(series):
            value = number(raw)
            if value <= 0:
                continue
            total += value
            stamp = stamps[i] if i < len(stamps) else ""
            day = stamp[:10]
            if day in days:
                days[day] += value
        if total > 0:
            totals[name] = total

    model_usage = {
        name: {"inputTokens": total, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
        for name, total in totals.items()
    }
    return {
        "todayTotalTokens": days.get(today.isoformat(), 0),
        "todayTokensByModel": {},
        "recentDays": [{"date": day, "messageCount": days[day]} for day in sorted(days)],
        "activeDays": len([d for d, v in days.items() if v > 0]),
        "activeDates": sorted(d for d, v in days.items() if v > 0),
        "modelUsage": model_usage,
    }
