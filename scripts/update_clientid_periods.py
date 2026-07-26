#!/usr/bin/env python3
"""Build exact privacy-safe ClientID summaries for selectable date ranges.

Raw ClientID values are used only inside the GitHub Action. Public files contain
counts and shares only; no raw or hashed identifiers are written.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import heapq
import io
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
import shutil
import urllib.error
import urllib.parse
import urllib.request

from update_fraud_data import (
    CAMPAIGN_TOKENS,
    CATALOG_PATH,
    COUNTER_IDS,
    DATA_DIR,
    START_DATE,
    campaign_in_scope,
    clean_log_request,
    field_value,
    read_json,
    request_json,
    source_name,
    valid_id,
    wait_for_log,
    write_json,
)

PERIOD_VERSION = 1
MAX_PERIOD_DAYS = 90
MIN_SOURCE_VISITS = 20
PERIOD_FIELDS = [
    "ym:s:date",
    "ym:s:clientID",
    "ym:s:<attribution>UTMSource",
    "ym:s:<attribution>UTMCampaign",
]


def write_compact_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def create_period_log_request(
    token: str,
    counter_id: int,
    start: dt.date,
    end: dt.date,
) -> int:
    params = {
        "date1": start.isoformat(),
        "date2": end.isoformat(),
        "source": "visits",
        "fields": ",".join(PERIOD_FIELDS),
        "attribution": "LAST",
    }
    url = (
        f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}/logrequests?"
        + urllib.parse.urlencode(params)
    )
    payload = request_json(url, token, method="POST")
    request_id = int((payload.get("log_request") or {}).get("request_id") or 0)
    if not request_id:
        raise RuntimeError(f"Logs API did not return period request_id for counter {counter_id}")
    return request_id


def empty_day() -> dict:
    return {"visits": 0, "clients": Counter()}


def download_period_parts(
    token: str,
    counter_id: int,
    request_id: int,
    parts: list[dict],
) -> tuple[dict[str, dict[str, dict]], int, int]:
    daily: dict[str, dict[str, dict]] = defaultdict(dict)
    raw_visits = 0
    included_visits = 0
    headers = {
        "Authorization": f"OAuth {token}",
        "Accept": "text/tab-separated-values",
        "User-Agent": "LevelTrafficFraudLab/0.9-periods",
    }

    for part in sorted(parts, key=lambda item: int(item.get("part_number") or 0)):
        part_number = int(part.get("part_number") or 0)
        url = (
            f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}"
            f"/logrequest/{request_id}/part/{part_number}/download"
        )
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                stream = io.TextIOWrapper(response, encoding="utf-8-sig", newline="")
                for row in csv.DictReader(stream, delimiter="\t"):
                    raw_visits += 1
                    campaign = field_value(
                        row,
                        "ym:s:<attribution>UTMCampaign",
                        "UTMCampaign",
                    )
                    if not campaign_in_scope(campaign):
                        continue
                    report_date = field_value(row, "ym:s:date").strip()
                    if not report_date:
                        continue
                    source = source_name(
                        field_value(row, "ym:s:<attribution>UTMSource", "UTMSource")
                    )
                    bucket = daily[source].setdefault(report_date, empty_day())
                    bucket["visits"] += 1
                    included_visits += 1
                    client_id = valid_id(field_value(row, "ym:s:clientID"))
                    if client_id:
                        bucket["clients"][client_id] += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Could not download ClientID period part {part_number} "
                f"for counter {counter_id}: {error}"
            ) from error
    return daily, raw_visits, included_visits


def fetch_clientid_history(
    token: str,
    counter_id: int,
    start: dt.date,
    end: dt.date,
) -> tuple[dict[str, dict[str, dict]], dict]:
    request_id = create_period_log_request(token, counter_id, start, end)
    try:
        log_request = wait_for_log(token, counter_id, request_id)
        daily, raw_visits, included_visits = download_period_parts(
            token,
            counter_id,
            request_id,
            list(log_request.get("parts") or []),
        )
    finally:
        clean_log_request(token, counter_id, request_id)
    return daily, {
        "requestId": request_id,
        "rawVisits": raw_visits,
        "includedVisits": included_visits,
        "excludedVisits": max(0, raw_visits - included_visits),
        "from": start.isoformat(),
        "to": end.isoformat(),
    }


def date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    days = (end - start).days
    return [start + dt.timedelta(days=offset) for offset in range(days + 1)]


def current_top_counts(
    counts: Counter[str],
    heap: list[tuple[int, str]],
    limit: int = 10,
) -> list[int]:
    selected: list[int] = []
    selected_ids: set[str] = set()
    restore: list[tuple[int, str]] = []
    while heap and len(selected) < limit:
        negative_count, client_id = heapq.heappop(heap)
        count = -negative_count
        if client_id in selected_ids or counts.get(client_id, 0) != count:
            continue
        selected_ids.add(client_id)
        selected.append(count)
        restore.append((negative_count, client_id))
    for item in restore:
        heapq.heappush(heap, item)
    return selected


def range_summary(
    *,
    visits: int,
    client_visits: int,
    unique_clients: int,
    top_counts: list[int],
    active_days: int,
) -> dict:
    top1_visits = top_counts[0] if top_counts else 0
    top10_visits = sum(top_counts[:10])
    coverage = client_visits / visits if visits else 0.0
    return {
        "visits": visits,
        "clientIdVisits": client_visits,
        "coverage": coverage,
        "uniqueClientIds": unique_clients,
        "top1Visits": top1_visits,
        "top1Share": top1_visits / client_visits if client_visits else 0.0,
        "top10Visits": top10_visits,
        "top10Share": top10_visits / client_visits if client_visits else 0.0,
        "visitsPerClientId": client_visits / unique_clients if unique_clients else 0.0,
        "repeatClientVisitShare": (
            max(0, client_visits - unique_clients) / client_visits
            if client_visits
            else 0.0
        ),
        "activeDays": active_days,
        "representative": (
            client_visits >= 300 and coverage >= 0.5 and unique_clients >= 20
        ),
    }


def build_period_payloads(
    counter_id: int,
    start: dt.date,
    end: dt.date,
    daily: dict[str, dict[str, dict]],
    generated_at: str,
) -> dict[str, dict]:
    dates = date_range(start, end)
    payloads = {
        day.isoformat(): {
            "version": PERIOD_VERSION,
            "counterId": counter_id,
            "from": day.isoformat(),
            "to": end.isoformat(),
            "generatedAt": generated_at,
            "method": "exact-clientid-period-v1",
            "campaignFilter": list(CAMPAIGN_TOKENS),
            "privacy": "Only counts and shares are public; raw and hashed ClientID values are not stored.",
            "ranges": {},
        }
        for day in dates
    }

    for source, source_days in sorted(daily.items()):
        day_buckets = [source_days.get(day.isoformat()) or empty_day() for day in dates]
        for start_index, start_day in enumerate(dates):
            cumulative: Counter[str] = Counter()
            heap: list[tuple[int, str]] = []
            visits = 0
            client_visits = 0
            active_days = 0
            payload = payloads[start_day.isoformat()]

            for end_index in range(start_index, len(dates)):
                bucket = day_buckets[end_index]
                day_visits = int(bucket.get("visits") or 0)
                if day_visits:
                    active_days += 1
                visits += day_visits
                clients: Counter[str] = bucket.get("clients") or Counter()
                client_visits += sum(clients.values())
                for client_id, increment in clients.items():
                    updated = cumulative.get(client_id, 0) + int(increment)
                    cumulative[client_id] = updated
                    heapq.heappush(heap, (-updated, client_id))

                if visits < MIN_SOURCE_VISITS:
                    continue
                end_text = dates[end_index].isoformat()
                top_counts = current_top_counts(cumulative, heap, 10)
                summary = range_summary(
                    visits=visits,
                    client_visits=client_visits,
                    unique_clients=len(cumulative),
                    top_counts=top_counts,
                    active_days=active_days,
                )
                payload["ranges"].setdefault(end_text, {})[source] = summary
    return payloads


def write_period_payloads(
    counter_id: int,
    payloads: dict[str, dict],
) -> None:
    output_dir = DATA_DIR / str(counter_id) / "clientid-periods"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for start_text, payload in sorted(payloads.items()):
        write_compact_json(output_dir / f"{start_text}.json", payload)


def patch_catalog(
    counter_id: int,
    start: dt.date,
    end: dt.date,
    generated_at: str,
) -> None:
    catalog = read_json(CATALOG_PATH, {})
    if not isinstance(catalog, dict):
        raise RuntimeError("Fraud catalog is unavailable or invalid")
    found = False
    for counter in catalog.get("counters") or []:
        if int(counter.get("id") or 0) != counter_id:
            continue
        counter["clientIdPeriods"] = {
            "version": PERIOD_VERSION,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "maxDays": MAX_PERIOD_DAYS,
            "pathTemplate": f"{counter_id}/clientid-periods/{{from}}.json",
            "method": "exact-clientid-period-v1",
        }
        found = True
        break
    if not found:
        raise RuntimeError(f"Counter {counter_id} is missing from fraud catalog")
    catalog["clientIdPeriodsGeneratedAt"] = generated_at
    catalog["clientIdPeriodModel"] = "exact-all-ranges-v1"
    write_json(CATALOG_PATH, catalog)


def self_test() -> None:
    start = dt.date(2026, 7, 1)
    end = dt.date(2026, 7, 2)
    daily = {
        "qbid": {
            "2026-07-01": {
                "visits": 10,
                "clients": Counter({"client-a": 8, "client-b": 2}),
            },
            "2026-07-02": {
                "visits": 10,
                "clients": Counter({"client-a": 4, "client-c": 6}),
            },
        }
    }
    payloads = build_period_payloads(53197618, start, end, daily, "test")
    result = payloads["2026-07-01"]["ranges"]["2026-07-02"]["qbid"]
    assert result["visits"] == 20
    assert result["clientIdVisits"] == 20
    assert result["uniqueClientIds"] == 3
    assert result["top1Visits"] == 12
    assert result["top1Share"] == 0.6
    assert result["top10Share"] == 1.0
    assert round(result["visitsPerClientId"], 6) == round(20 / 3, 6)
    serialized = json.dumps(payloads, ensure_ascii=False)
    assert "client-a" not in serialized
    assert "client-b" not in serialized
    print("ClientID period summary self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counter-id", type=int, action="append")
    parser.add_argument("--date-to", type=dt.date.fromisoformat)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return

    token = os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("YANDEX_METRIKA_TOKEN is not configured")

    today_moscow = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
    end = min(args.date_to or today_moscow - dt.timedelta(days=1), today_moscow - dt.timedelta(days=1))
    start = max(START_DATE, end - dt.timedelta(days=MAX_PERIOD_DAYS - 1))
    counter_ids = tuple(args.counter_id or COUNTER_IDS)
    unknown = sorted(set(counter_ids).difference(COUNTER_IDS))
    if unknown:
        raise RuntimeError(f"Unsupported counter IDs: {unknown}")

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    for counter_id in counter_ids:
        daily, operation = fetch_clientid_history(token, counter_id, start, end)
        payloads = build_period_payloads(counter_id, start, end, daily, generated_at)
        write_period_payloads(counter_id, payloads)
        patch_catalog(counter_id, start, end, generated_at)
        print(
            json.dumps(
                {
                    "counterId": counter_id,
                    "periodFiles": len(payloads),
                    "sources": len(daily),
                    **operation,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
