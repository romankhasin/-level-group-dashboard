#!/usr/bin/env python3
"""Diagnostic test for Target Ads Aggregated API IVT metrics.

Writes only a small diagnostic summary; it does not modify dashboard data.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "targetads_agg_ivt_test.json"
API = "https://api.targetads.io/v1/reports/agg_report"
METRICS = ["Impressions", "Clicks", "GIVT", "SIVT", "PartOfGIVT"]


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def column_name(column: object, index: int) -> str:
    if isinstance(column, str):
        return column
    if isinstance(column, dict):
        for key in ("Name", "name", "Field", "field", "Column", "column", "Key", "key"):
            value = column.get(key)
            if value:
                return str(value)
    return f"column_{index}"


def normalize_rows(parsed: object) -> tuple[list[dict], list[str], object]:
    if not isinstance(parsed, dict):
        return [], [], None

    documented = parsed.get("data")
    if isinstance(documented, list):
        rows = [row for row in documented if isinstance(row, dict)]
        columns = sorted({key for row in rows for key in row.keys()})
        return rows, columns, documented[:2]

    raw_rows = parsed.get("Rows")
    raw_columns = parsed.get("Columns")
    if not isinstance(raw_rows, list):
        return [], [], None

    if raw_rows and all(isinstance(row, dict) for row in raw_rows):
        rows = [row for row in raw_rows if isinstance(row, dict)]
        columns = sorted({key for row in rows for key in row.keys()})
        return rows, columns, raw_rows[:2]

    columns: list[str] = []
    if isinstance(raw_columns, list):
        columns = [column_name(column, index) for index, column in enumerate(raw_columns)]

    rows: list[dict] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, (list, tuple)):
            continue
        if not columns:
            columns = [f"column_{index}" for index in range(len(raw_row))]
        rows.append({columns[index] if index < len(columns) else f"column_{index}": value for index, value in enumerate(raw_row)})
    return rows, columns, raw_rows[:2]


def run_query(token: str, project_id: int, name: str, date_from: str, date_to: str, fields: list[str]) -> dict:
    payload = {
        "ResponseType": "JSON",
        "Fields": fields,
        "MediaMetrics": METRICS,
        "InteractionFilter": {"DateFrom": date_from, "DateTo": date_to},
        "AttributionModel": "mli",
        "AttributionWindow": "30",
        "DateGrouping": "day",
        "Offset": 0,
        "Limit": 100000,
    }
    url = API + "?" + urllib.parse.urlencode({"project_id": project_id})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body[:2000]}
        return {
            "name": name,
            "period": {"from": date_from, "to": date_to},
            "fields": fields,
            "httpStatus": error.code,
            "ok": False,
            "error": parsed,
        }
    except Exception as error:
        return {
            "name": name,
            "period": {"from": date_from, "to": date_to},
            "fields": fields,
            "httpStatus": None,
            "ok": False,
            "error": {"type": type(error).__name__, "message": str(error)},
        }

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {
            "name": name,
            "period": {"from": date_from, "to": date_to},
            "fields": fields,
            "httpStatus": status,
            "ok": False,
            "error": {"message": "Non-JSON response", "raw": body[:2000]},
        }

    rows, columns, raw_sample = normalize_rows(parsed)
    totals = {metric: sum(number(row.get(metric)) for row in rows) for metric in METRICS}
    present = {
        metric: sum(1 for row in rows if metric in row and row.get(metric) is not None)
        for metric in METRICS
    }
    nonzero = {
        metric: sum(1 for row in rows if number(row.get(metric)) != 0)
        for metric in METRICS
    }
    api_count = None
    if isinstance(parsed, dict):
        api_count = parsed.get("count", parsed.get("CountRows"))

    return {
        "name": name,
        "period": {"from": date_from, "to": date_to},
        "fields": fields,
        "httpStatus": status,
        "ok": status == 200 and isinstance(parsed, dict),
        "apiCount": api_count,
        "rowCount": len(rows),
        "columns": columns,
        "metricFieldPresence": present,
        "metricNonZeroRows": nonzero,
        "totals": totals,
        "sample": rows[:8],
        "rawRowSample": raw_sample if not rows else None,
        "responseKeys": sorted(parsed.keys()) if isinstance(parsed, dict) else [],
    }


def main() -> None:
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    if not token:
        result = {
            "generatedAt": generated_at,
            "projectId": project_id,
            "ok": False,
            "error": "TARGETADS_TOKEN is not configured",
            "tests": [],
        }
    else:
        tests = [
            run_query(token, project_id, "august_1_16_day_campaign", "2026-08-01", "2026-08-16", ["EventDate", "MediaCampaign"]),
            run_query(token, project_id, "august_1_16_source_campaign", "2026-08-01", "2026-08-16", ["MediaSource", "MediaCampaign"]),
            run_query(token, project_id, "recent_25_26_day_campaign", "2026-08-25", "2026-08-26", ["EventDate", "MediaCampaign"]),
        ]
        result = {
            "generatedAt": generated_at,
            "projectId": project_id,
            "endpoint": API,
            "requestedMetrics": METRICS,
            "ok": any(test.get("ok") and test.get("rowCount", 0) > 0 for test in tests),
            "tests": tests,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
