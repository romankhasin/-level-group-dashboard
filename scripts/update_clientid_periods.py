#!/usr/bin/env python3
"""Build exact privacy-safe concentration summaries for selectable date ranges.

Raw ClientID and IP values are used only inside the GitHub Action. Public files
contain counts, shares and non-sensitive technical labels only; no raw or hashed
identifiers are written.
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
    subnet_of,
    valid_id,
    wait_for_log,
    write_json,
)

PERIOD_VERSION = 2
MAX_PERIOD_DAYS = 90
MIN_SOURCE_VISITS = 20
PERIOD_FIELDS = [
    "ym:s:date",
    "ym:s:clientID",
    "ym:s:ipAddress",
    "ym:s:<attribution>UTMSource",
    "ym:s:<attribution>UTMCampaign",
    "ym:s:browser",
    "ym:s:browserMajorVersion",
    "ym:s:browserMinorVersion",
    "ym:s:operatingSystem",
    "ym:s:deviceCategory",
    "ym:s:screenWidth",
    "ym:s:screenHeight",
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
    return {
        "visits": 0,
        "clients": Counter(),
        "ips": Counter(),
        "subnets": Counter(),
        "browsers": Counter(),
        "profiles": Counter(),
    }


def technical_labels(row: dict[str, str]) -> tuple[str, str]:
    browser = field_value(row, "ym:s:browser").strip() or "Не определено"
    major = field_value(row, "ym:s:browserMajorVersion").strip()
    minor = field_value(row, "ym:s:browserMinorVersion").strip()
    version = ".".join(part for part in (major, minor) if part and part != "0")
    browser_version = f"{browser} {version}".strip()
    operating_system = (
        field_value(row, "ym:s:operatingSystem").strip() or "Не определено"
    )
    device = field_value(row, "ym:s:deviceCategory").strip() or "Не определено"
    width = field_value(row, "ym:s:screenWidth").strip()
    height = field_value(row, "ym:s:screenHeight").strip()
    resolution = f"{width}x{height}" if width and height else "Не определено"
    profile = f"{browser_version} · {operating_system} · {device} · {resolution}"
    return browser_version, profile


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
        "User-Agent": "LevelTrafficFraudLab/1.0-periods",
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

                    ip = valid_id(field_value(row, "ym:s:ipAddress"))
                    if ip:
                        bucket["ips"][ip] += 1
                        bucket["subnets"][subnet_of(ip)] += 1

                    browser, profile = technical_labels(row)
                    bucket["browsers"][browser] += 1
                    bucket["profiles"][profile] += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Could not download concentration period part {part_number} "
                f"for counter {counter_id}: {error}"
            ) from error
    return daily, raw_visits, included_visits


def fetch_period_history(
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


def update_counter_heap(
    cumulative: Counter[str],
    heap: list[tuple[int, str]],
    increments: Counter[str],
) -> None:
    for key, increment in increments.items():
        updated = cumulative.get(key, 0) + int(increment)
        cumulative[key] = updated
        heapq.heappush(heap, (-updated, key))


def current_top_entries(
    counts: Counter[str],
    heap: list[tuple[int, str]],
    limit: int = 10,
) -> list[tuple[str, int]]:
    selected: list[tuple[str, int]] = []
    selected_keys: set[str] = set()
    restore: list[tuple[int, str]] = []
    while heap and len(selected) < limit:
        negative_count, key = heapq.heappop(heap)
        count = -negative_count
        if key in selected_keys or counts.get(key, 0) != count:
            continue
        selected_keys.add(key)
        selected.append((key, count))
        restore.append((negative_count, key))
    for item in restore:
        heapq.heappush(heap, item)
    return selected


def range_summary(
    *,
    visits: int,
    client_visits: int,
    unique_clients: int,
    client_entries: list[tuple[str, int]],
    ip_entries: list[tuple[str, int]],
    subnet_entries: list[tuple[str, int]],
    browser_entries: list[tuple[str, int]],
    profile_entries: list[tuple[str, int]],
    active_days: int,
) -> dict:
    top1_visits = client_entries[0][1] if client_entries else 0
    top10_visits = sum(count for _, count in client_entries[:10])
    top_ip_visits = ip_entries[0][1] if ip_entries else 0
    top_subnet_visits = subnet_entries[0][1] if subnet_entries else 0
    top_browser, top_browser_visits = (
        browser_entries[0] if browser_entries else ("—", 0)
    )
    top_profile, top_profile_visits = (
        profile_entries[0] if profile_entries else ("—", 0)
    )
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
        "topIpVisits": top_ip_visits,
        "topIpShare": top_ip_visits / visits if visits else 0.0,
        "topSubnetVisits": top_subnet_visits,
        "topSubnetShare": top_subnet_visits / visits if visits else 0.0,
        "topBrowser": top_browser,
        "topBrowserVisits": top_browser_visits,
        "topBrowserShare": top_browser_visits / visits if visits else 0.0,
        "topProfile": top_profile,
        "topProfileVisits": top_profile_visits,
        "topProfileShare": top_profile_visits / visits if visits else 0.0,
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
            "method": "exact-concentration-period-v2",
            "campaignFilter": list(CAMPAIGN_TOKENS),
            "privacy": (
                "Only counts, shares and non-sensitive technical labels are public; "
                "raw and hashed ClientID and IP values are not stored."
            ),
            "ranges": {},
        }
        for day in dates
    }

    counter_names = ("clients", "ips", "subnets", "browsers", "profiles")
    for source, source_days in sorted(daily.items()):
        day_buckets = [source_days.get(day.isoformat()) or empty_day() for day in dates]
        for start_index, start_day in enumerate(dates):
            cumulative = {name: Counter() for name in counter_names}
            heaps: dict[str, list[tuple[int, str]]] = {
                name: [] for name in counter_names
            }
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

                for name in counter_names:
                    increments: Counter[str] = bucket.get(name) or Counter()
                    update_counter_heap(cumulative[name], heaps[name], increments)
                client_visits += sum((bucket.get("clients") or Counter()).values())

                if visits < MIN_SOURCE_VISITS:
                    continue

                end_text = dates[end_index].isoformat()
                summary = range_summary(
                    visits=visits,
                    client_visits=client_visits,
                    unique_clients=len(cumulative["clients"]),
                    client_entries=current_top_entries(
                        cumulative["clients"], heaps["clients"], 10
                    ),
                    ip_entries=current_top_entries(cumulative["ips"], heaps["ips"], 1),
                    subnet_entries=current_top_entries(
                        cumulative["subnets"], heaps["subnets"], 1
                    ),
                    browser_entries=current_top_entries(
                        cumulative["browsers"], heaps["browsers"], 1
                    ),
                    profile_entries=current_top_entries(
                        cumulative["profiles"], heaps["profiles"], 1
                    ),
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
    info = {
        "version": PERIOD_VERSION,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "maxDays": MAX_PERIOD_DAYS,
        "pathTemplate": f"{counter_id}/clientid-periods/{{from}}.json",
        "method": "exact-concentration-period-v2",
    }
    for counter in catalog.get("counters") or []:
        if int(counter.get("id") or 0) != counter_id:
            continue
        counter["periodMetrics"] = dict(info)
        counter["clientIdPeriods"] = dict(info)
        found = True
        break
    if not found:
        raise RuntimeError(f"Counter {counter_id} is missing from fraud catalog")
    catalog["periodMetricsGeneratedAt"] = generated_at
    catalog["periodMetricsModel"] = "exact-all-ranges-concentration-v2"
    catalog["clientIdPeriodsGeneratedAt"] = generated_at
    catalog["clientIdPeriodModel"] = "exact-all-ranges-concentration-v2"
    write_json(CATALOG_PATH, catalog)


def self_test() -> None:
    start = dt.date(2026, 7, 1)
    end = dt.date(2026, 7, 2)
    daily = {
        "qbid": {
            "2026-07-01": {
                "visits": 10,
                "clients": Counter({"client-a": 8, "client-b": 2}),
                "ips": Counter({"10.20.30.40": 8, "10.20.30.41": 2}),
                "subnets": Counter({"10.20.30.0/24": 10}),
                "browsers": Counter({"Chrome 149": 7, "Safari 18": 3}),
                "profiles": Counter(
                    {
                        "Chrome 149 · Android · 2 · 412x892": 6,
                        "Safari 18 · iOS · 2 · 390x844": 4,
                    }
                ),
            },
            "2026-07-02": {
                "visits": 10,
                "clients": Counter({"client-a": 4, "client-c": 6}),
                "ips": Counter({"10.20.30.40": 4, "10.20.31.50": 6}),
                "subnets": Counter({"10.20.30.0/24": 4, "10.20.31.0/24": 6}),
                "browsers": Counter({"Chrome 149": 8, "Safari 18": 2}),
                "profiles": Counter(
                    {
                        "Chrome 149 · Android · 2 · 412x892": 8,
                        "Safari 18 · iOS · 2 · 390x844": 2,
                    }
                ),
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
    assert result["topIpVisits"] == 12
    assert result["topIpShare"] == 0.6
    assert result["topSubnetVisits"] == 14
    assert result["topSubnetShare"] == 0.7
    assert result["topBrowser"] == "Chrome 149"
    assert result["topBrowserVisits"] == 15
    assert result["topBrowserShare"] == 0.75
    assert result["topProfile"] == "Chrome 149 · Android · 2 · 412x892"
    assert result["topProfileVisits"] == 14
    assert result["topProfileShare"] == 0.7
    serialized = json.dumps(payloads, ensure_ascii=False)
    assert "client-a" not in serialized
    assert "client-b" not in serialized
    assert "10.20.30.40" not in serialized
    assert "10.20.30.0/24" not in serialized
    print("Exact concentration period summary self-test passed")


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
    end = min(
        args.date_to or today_moscow - dt.timedelta(days=1),
        today_moscow - dt.timedelta(days=1),
    )
    start = max(START_DATE, end - dt.timedelta(days=MAX_PERIOD_DAYS - 1))
    counter_ids = tuple(args.counter_id or COUNTER_IDS)
    unknown = sorted(set(counter_ids).difference(COUNTER_IDS))
    if unknown:
        raise RuntimeError(f"Unsupported counter IDs: {unknown}")

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    for counter_id in counter_ids:
        daily, operation = fetch_period_history(token, counter_id, start, end)
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
