#!/usr/bin/env python3
"""Backfill exact calendar-month fraud metrics without replacing rolling 90-day data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import update_clientid_periods as periods
from update_fraud_data import CATALOG_PATH, COUNTER_IDS, DATA_DIR, read_json, write_json


def month_dates(month: str) -> tuple[dt.date, dt.date]:
    start = dt.date.fromisoformat(f"{month}-01")
    if start.year != 2026 or start.month not in range(1, 5):
        raise ValueError("Only January-April 2026 are supported")
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start, end - dt.timedelta(days=1)


def month_files_ready(counter_id: int, month: str, end: dt.date) -> bool:
    period_path = DATA_DIR / str(counter_id) / "clientid-periods" / f"{month}-01.json"
    slice_path = DATA_DIR / str(counter_id) / "slices" / f"{month}.json"
    period = read_json(period_path, {})
    slices = read_json(slice_path, {})
    return (
        isinstance(period, dict)
        and period.get("version") == periods.PERIOD_VERSION
        and period.get("from") == f"{month}-01"
        and period.get("to") == end.isoformat()
        and bool((period.get("ranges") or {}).get(end.isoformat()))
        and isinstance(slices, dict)
        and slices.get("version") == periods.SLICE_VERSION
        and slices.get("month") == month
        and bool(slices.get("groups"))
    )


def patch_catalog(month: str, end: dt.date, generated_at: str) -> None:
    catalog = read_json(CATALOG_PATH, {})
    if not isinstance(catalog, dict):
        raise RuntimeError("Fraud catalog is missing")
    found = set()
    for counter in catalog.get("counters") or []:
        counter_id = int(counter.get("id") or 0)
        if counter_id not in COUNTER_IDS:
            continue
        if not month_files_ready(counter_id, month, end):
            raise RuntimeError(f"Monthly files are incomplete for counter {counter_id}")
        found.add(counter_id)
        previous = counter.get("monthlyPeriodMetrics") or {}
        months = sorted(set(previous.get("months") or []) | {month})
        info = {
            "version": periods.PERIOD_VERSION,
            "from": f"{months[0]}-01",
            "to": end.isoformat() if month == months[-1] else month_dates(months[-1])[1].isoformat(),
            "maxDays": 31,
            "months": months,
            "pathTemplate": f"{counter_id}/clientid-periods/{{from}}.json",
            "method": "exact-period-metrics-v4-cookie-segments",
            "scope": "calendar-months-only",
        }
        counter["monthlyPeriodMetrics"] = info
        counter["monthlySliceMetrics"] = {
            "version": periods.SLICE_VERSION,
            "from": info["from"],
            "to": info["to"],
            "maxDays": 31,
            "months": months,
            "pathTemplate": f"{counter_id}/slices/{{month}}.json",
            "method": "safe-daily-slices-v1",
            "scope": "calendar-months-only",
            "dimensions": list(periods.SLICE_DIMENSIONS),
        }
    if found != set(COUNTER_IDS):
        raise RuntimeError(f"Missing counters in fraud catalog: {set(COUNTER_IDS) - found}")
    catalog["monthlyPeriodMetricsGeneratedAt"] = generated_at
    catalog["monthlySliceMetricsGeneratedAt"] = generated_at
    write_json(CATALOG_PATH, catalog)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True)
    args = parser.parse_args()
    start, end = month_dates(args.month)
    token = os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("YANDEX_METRIKA_TOKEN is not configured")
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    for counter_id in COUNTER_IDS:
        if month_files_ready(counter_id, args.month, end):
            print(json.dumps({"counterId": counter_id, "month": args.month, "status": "ready"}), flush=True)
            continue
        daily, operation = periods.fetch_period_history(token, counter_id, start, end)
        period_payload = periods.build_period_payloads(
            counter_id, start, end, daily, generated_at
        )[start.isoformat()]
        slice_payload = periods.build_slice_payloads(
            counter_id, daily, generated_at
        ).get(args.month)
        if not period_payload["ranges"].get(end.isoformat()) or not slice_payload or not slice_payload["groups"]:
            raise RuntimeError(f"No complete monthly metrics for counter {counter_id} in {args.month}")
        base = DATA_DIR / str(counter_id)
        periods.write_compact_json(
            base / "clientid-periods" / f"{start.isoformat()}.json", period_payload
        )
        periods.write_compact_json(base / "slices" / f"{args.month}.json", slice_payload)
        print(json.dumps({"counterId": counter_id, "month": args.month, **operation}), flush=True)

    patch_catalog(args.month, end, generated_at)
    print(json.dumps({"month": args.month, "status": "complete"}), flush=True)


if __name__ == "__main__":
    main()
