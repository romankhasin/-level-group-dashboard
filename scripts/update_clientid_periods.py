#!/usr/bin/env python3
"""Build privacy-safe period metrics and drill-down slices for Fraud Lab.

Raw ClientID and IP values are used only inside the GitHub Action. Public files
contain counts, shares, referrer hostnames and non-sensitive technical labels;
raw or hashed identifiers are never written.
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
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

from update_fraud_data import (
    CAMPAIGN_TOKENS,
    CATALOG_PATH,
    COUNTER_IDS,
    DATA_DIR,
    QUALITY_CALL_GOAL_IDS,
    START_DATE,
    campaign_in_scope,
    clean_log_request,
    field_value,
    read_json,
    request_json,
    safe_int,
    source_name,
    subnet_of,
    valid_id,
    wait_for_log,
    write_json,
)

PERIOD_VERSION = 3
SLICE_VERSION = 1
MAX_PERIOD_DAYS = 90
MIN_SOURCE_VISITS = 20
SLICE_MIN_MONTH_VISITS = 10
SLICE_MIN_MONTH_SHARE = 0.01
SLICE_DIMENSIONS = (
    "browser",
    "resolution",
    "os",
    "deviceModel",
    "referrer",
    "browserResolution",
    "fingerprint",
)
SLICE_DAY_FIELDS = (
    "date",
    "visits",
    "bounceVisits",
    "durationSum",
    "pageViewsSum",
    "qualityVisits",
    "newVisits",
)
PERIOD_FIELDS = [
    "ym:s:date",
    "ym:s:dateTimeUTC",
    "ym:s:clientID",
    "ym:s:ipAddress",
    "ym:s:<attribution>UTMSource",
    "ym:s:<attribution>UTMCampaign",
    "ym:s:referer",
    "ym:s:bounce",
    "ym:s:visitDuration",
    "ym:s:pageViews",
    "ym:s:isNewUser",
    "ym:s:goalsID",
    "ym:s:goalsDateTime",
    "ym:s:browser",
    "ym:s:browserMajorVersion",
    "ym:s:browserMinorVersion",
    "ym:s:operatingSystem",
    "ym:s:deviceCategory",
    "ym:s:mobilePhone",
    "ym:s:mobilePhoneModel",
    "ym:s:screenWidth",
    "ym:s:screenHeight",
    "ym:s:cookieEnabled",
]
UNKNOWN_RE = re.compile(r"не определ|unknown|undefined|other|другие|not set", re.I)
DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.+-]\d+|Z)?")


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


def empty_stats() -> dict:
    return {
        "visits": 0,
        "bounceVisits": 0,
        "durationSum": 0,
        "pageViewsSum": 0,
        "qualityVisits": 0,
        "newVisits": 0,
    }


def add_stats(target: dict, source: dict) -> None:
    for key in empty_stats():
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)


def visit_stats(*, bounce: bool, duration: int, pageviews: int, quality: bool, is_new: bool) -> dict:
    return {
        "visits": 1,
        "bounceVisits": int(bool(bounce)),
        "durationSum": max(0, int(duration or 0)),
        "pageViewsSum": max(0, int(pageviews or 0)),
        "qualityVisits": int(bool(quality)),
        "newVisits": int(bool(is_new)),
    }


def empty_day() -> dict:
    return {
        "visits": 0,
        "metrics": empty_stats(),
        "clients": Counter(),
        "bounceClients": Counter(),
        "ips": Counter(),
        "subnets": Counter(),
        "browsers": Counter(),
        "profiles": Counter(),
        "groups": {dimension: {} for dimension in SLICE_DIMENSIONS},
        "groupMeta": {dimension: {} for dimension in SLICE_DIMENSIONS},
        "fastAnyGoal15Visits": 0,
        "fastAnyGoal30Visits": 0,
        "fastQualityGoal15Visits": 0,
        "fastQualityGoal30Visits": 0,
        "multiGoalVisits": 0,
        "zeroResolutionVisits": 0,
        "unknownResolutionVisits": 0,
        "unknownBrowserVisits": 0,
        "unknownOsVisits": 0,
        "unknownModelVisits": 0,
        "missingReferrerVisits": 0,
        "ipv6Visits": 0,
        "cookieDisabledVisits": 0,
    }


def add_group(bucket: dict, dimension: str, value: str, stats: dict, meta: dict | None = None) -> None:
    value = str(value or "Не определено").strip() or "Не определено"
    groups = bucket["groups"][dimension]
    target = groups.setdefault(value, empty_stats())
    add_stats(target, stats)
    if meta and value not in bucket["groupMeta"][dimension]:
        bucket["groupMeta"][dimension][value] = dict(meta)


def technical_labels(row: dict[str, str]) -> dict:
    browser = field_value(row, "ym:s:browser").strip() or "Не определено"
    major = field_value(row, "ym:s:browserMajorVersion").strip()
    minor = field_value(row, "ym:s:browserMinorVersion").strip()
    version = ".".join(part for part in (major, minor) if part and part != "0")
    browser_version = f"{browser} {version}".strip()
    operating_system = field_value(row, "ym:s:operatingSystem").strip() or "Не определено"
    device_category = field_value(row, "ym:s:deviceCategory").strip() or "Не определено"
    manufacturer = field_value(row, "ym:s:mobilePhone").strip()
    model = field_value(row, "ym:s:mobilePhoneModel").strip()
    if manufacturer or model:
        device_model = " ".join(part for part in (manufacturer, model) if part).strip()
    elif device_category in {"2", "3"}:
        device_model = "Не определено"
    else:
        device_model = "Не применимо"
    width = field_value(row, "ym:s:screenWidth").strip()
    height = field_value(row, "ym:s:screenHeight").strip()
    if width and height:
        resolution = f"{width}x{height}"
    else:
        resolution = "Не определено"
    browser_resolution = f"{browser_version} · {resolution}"
    profile_parts = [browser_version, operating_system, device_category, resolution]
    if device_model != "Не применимо":
        profile_parts.append(device_model)
    fingerprint = " · ".join(profile_parts)
    return {
        "browser": browser_version,
        "os": operating_system,
        "deviceCategory": device_category,
        "deviceModel": device_model,
        "resolution": resolution,
        "browserResolution": browser_resolution,
        "fingerprint": fingerprint,
    }


def referrer_hostname(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"0", "undefined", "none", "not set", "(not set)"}:
        return "Не определено"
    try:
        parsed = urllib.parse.urlparse(raw if "://" in raw else f"//{raw}")
        host = (parsed.hostname or "").strip().lower()
    except ValueError:
        host = ""
    if not host:
        host = raw.split("/")[0].split("?")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "Не определено"


def parse_int_array(value: object) -> list[int]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [int(item) for item in parsed if str(item).strip().isdigit()]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return [int(item) for item in re.findall(r"\d+", text)]


def parse_datetime(value: object) -> dt.datetime | None:
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone(dt.timedelta(hours=3))).replace(tzinfo=None)
    return parsed


def parse_datetime_array(value: object) -> list[dt.datetime | None]:
    text = str(value or "").strip()
    if not text:
        return []
    values: list[object] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            values = parsed
    except (json.JSONDecodeError, TypeError):
        values = DATETIME_RE.findall(text)
    if not values:
        values = DATETIME_RE.findall(text)
    return [parse_datetime(item) for item in values]


def goal_speed_flags(row: dict[str, str], quality_goal_id: int) -> dict:
    start = parse_datetime(field_value(row, "ym:s:dateTimeUTC"))
    goal_ids = parse_int_array(field_value(row, "ym:s:goalsID"))
    goal_times = parse_datetime_array(field_value(row, "ym:s:goalsDateTime"))
    any_seconds: list[float] = []
    quality_seconds: list[float] = []
    if start:
        for goal_id, goal_time in zip(goal_ids, goal_times):
            if not goal_time:
                continue
            seconds = (goal_time - start).total_seconds()
            if seconds < 0 or seconds > 86400:
                continue
            any_seconds.append(seconds)
            if goal_id == quality_goal_id:
                quality_seconds.append(seconds)
    minimum_any = min(any_seconds) if any_seconds else None
    minimum_quality = min(quality_seconds) if quality_seconds else None
    return {
        "fastAnyGoal15Visits": int(minimum_any is not None and minimum_any <= 15),
        "fastAnyGoal30Visits": int(minimum_any is not None and 15 < minimum_any <= 30),
        "fastQualityGoal15Visits": int(minimum_quality is not None and minimum_quality <= 15),
        "fastQualityGoal30Visits": int(minimum_quality is not None and 15 < minimum_quality <= 30),
        "multiGoalVisits": int(len(set(goal_ids)) >= 3),
    }


def process_period_row(
    daily: dict[str, dict[str, dict]],
    row: dict[str, str],
    *,
    quality_goal_id: int,
) -> bool:
    campaign = field_value(row, "ym:s:<attribution>UTMCampaign", "UTMCampaign")
    if not campaign_in_scope(campaign):
        return False
    report_date = field_value(row, "ym:s:date").strip()
    if not report_date:
        return False
    source = source_name(field_value(row, "ym:s:<attribution>UTMSource", "UTMSource"))
    bucket = daily[source].setdefault(report_date, empty_day())
    bucket["visits"] += 1

    bounce = bool(safe_int(field_value(row, "ym:s:bounce")))
    duration = max(0, safe_int(field_value(row, "ym:s:visitDuration")))
    pageviews = max(0, safe_int(field_value(row, "ym:s:pageViews")))
    is_new = bool(safe_int(field_value(row, "ym:s:isNewUser")))
    goal_ids = set(parse_int_array(field_value(row, "ym:s:goalsID")))
    quality = quality_goal_id in goal_ids
    stats = visit_stats(
        bounce=bounce,
        duration=duration,
        pageviews=pageviews,
        quality=quality,
        is_new=is_new,
    )
    add_stats(bucket["metrics"], stats)

    client_id = valid_id(field_value(row, "ym:s:clientID"))
    if client_id:
        bucket["clients"][client_id] += 1
        if bounce:
            bucket["bounceClients"][client_id] += 1

    ip = valid_id(field_value(row, "ym:s:ipAddress"))
    if ip:
        bucket["ips"][ip] += 1
        bucket["subnets"][subnet_of(ip)] += 1
        if ":" in ip:
            bucket["ipv6Visits"] += 1

    labels = technical_labels(row)
    bucket["browsers"][labels["browser"]] += 1
    bucket["profiles"][labels["fingerprint"]] += 1
    referrer = referrer_hostname(field_value(row, "ym:s:referer"))

    add_group(bucket, "browser", labels["browser"], stats)
    add_group(bucket, "resolution", labels["resolution"], stats)
    add_group(bucket, "os", labels["os"], stats)
    if labels["deviceModel"] != "Не применимо":
        add_group(bucket, "deviceModel", labels["deviceModel"], stats)
    add_group(bucket, "referrer", referrer, stats)
    add_group(
        bucket,
        "browserResolution",
        labels["browserResolution"],
        stats,
        {"browser": labels["browser"], "resolution": labels["resolution"]},
    )
    add_group(
        bucket,
        "fingerprint",
        labels["fingerprint"],
        stats,
        {
            "browser": labels["browser"],
            "resolution": labels["resolution"],
            "os": labels["os"],
            "deviceCategory": labels["deviceCategory"],
            "deviceModel": labels["deviceModel"],
        },
    )

    if labels["resolution"] == "0x0":
        bucket["zeroResolutionVisits"] += 1
    if labels["resolution"] == "Не определено":
        bucket["unknownResolutionVisits"] += 1
    if UNKNOWN_RE.search(labels["browser"]):
        bucket["unknownBrowserVisits"] += 1
    if UNKNOWN_RE.search(labels["os"]):
        bucket["unknownOsVisits"] += 1
    if labels["deviceCategory"] in {"2", "3"} and UNKNOWN_RE.search(labels["deviceModel"]):
        bucket["unknownModelVisits"] += 1
    if referrer == "Не определено":
        bucket["missingReferrerVisits"] += 1
    if not bool(safe_int(field_value(row, "ym:s:cookieEnabled"))):
        bucket["cookieDisabledVisits"] += 1

    for key, value in goal_speed_flags(row, quality_goal_id).items():
        bucket[key] += int(value)
    return True


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
        "User-Agent": "LevelTrafficFraudLab/1.1-periods",
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
                    if process_period_row(
                        daily,
                        row,
                        quality_goal_id=QUALITY_CALL_GOAL_IDS[counter_id],
                    ):
                        included_visits += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Could not download period part {part_number} for counter {counter_id}: {error}"
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


def metric_rates(metrics: dict) -> dict:
    visits = int(metrics.get("visits") or 0)
    return {
        "bounce": int(metrics.get("bounceVisits") or 0) / visits if visits else 0.0,
        "time": int(metrics.get("durationSum") or 0) / visits if visits else 0.0,
        "depth": int(metrics.get("pageViewsSum") or 0) / visits if visits else 0.0,
        "quality": int(metrics.get("qualityVisits") or 0) / visits if visits else 0.0,
        "newShare": int(metrics.get("newVisits") or 0) / visits if visits else 0.0,
    }


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
    metrics: dict,
    repeat_bounce_clients5: int,
    behavior: dict,
    technical_quality: dict,
    active_days: int,
) -> dict:
    top1_visits = client_entries[0][1] if client_entries else 0
    top10_visits = sum(count for _, count in client_entries[:10])
    top_ip_visits = ip_entries[0][1] if ip_entries else 0
    top_subnet_visits = subnet_entries[0][1] if subnet_entries else 0
    top_browser, top_browser_visits = browser_entries[0] if browser_entries else ("—", 0)
    top_profile, top_profile_visits = profile_entries[0] if profile_entries else ("—", 0)
    coverage = client_visits / visits if visits else 0.0
    rates = metric_rates(metrics)
    result = {
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
            max(0, client_visits - unique_clients) / client_visits if client_visits else 0.0
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
        "repeatBounceClients5": repeat_bounce_clients5,
        "repeatBounceClientShare": repeat_bounce_clients5 / unique_clients if unique_clients else 0.0,
        "activeDays": active_days,
        "representative": client_visits >= 300 and coverage >= 0.5 and unique_clients >= 20,
        **rates,
    }
    for key, value in behavior.items():
        result[key] = int(value)
        result[key.replace("Visits", "Share")] = int(value) / visits if visits else 0.0
    for key, value in technical_quality.items():
        result[key] = int(value)
        result[key.replace("Visits", "Share")] = int(value) / visits if visits else 0.0
    return result


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
            "method": "exact-period-metrics-v3",
            "campaignFilter": list(CAMPAIGN_TOKENS),
            "privacy": (
                "Only counts, shares, referrer hostnames and non-sensitive technical labels "
                "are public; raw and hashed ClientID and IP values are not stored."
            ),
            "ranges": {},
        }
        for day in dates
    }

    counter_names = ("clients", "ips", "subnets", "browsers", "profiles")
    behavior_names = (
        "fastAnyGoal15Visits",
        "fastAnyGoal30Visits",
        "fastQualityGoal15Visits",
        "fastQualityGoal30Visits",
        "multiGoalVisits",
    )
    technical_names = (
        "zeroResolutionVisits",
        "unknownResolutionVisits",
        "unknownBrowserVisits",
        "unknownOsVisits",
        "unknownModelVisits",
        "missingReferrerVisits",
        "ipv6Visits",
        "cookieDisabledVisits",
    )

    for source, source_days in sorted(daily.items()):
        day_buckets = [source_days.get(day.isoformat()) or empty_day() for day in dates]
        for start_index, start_day in enumerate(dates):
            cumulative = {name: Counter() for name in counter_names}
            heaps: dict[str, list[tuple[int, str]]] = {name: [] for name in counter_names}
            cumulative_metrics = empty_stats()
            cumulative_bounce_clients: Counter[str] = Counter()
            repeat_bounce_clients5 = 0
            behavior = {name: 0 for name in behavior_names}
            technical_quality = {name: 0 for name in technical_names}
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
                add_stats(cumulative_metrics, bucket.get("metrics") or empty_stats())

                for name in counter_names:
                    increments: Counter[str] = bucket.get(name) or Counter()
                    update_counter_heap(cumulative[name], heaps[name], increments)
                client_visits += sum((bucket.get("clients") or Counter()).values())

                for client_id, increment in (bucket.get("bounceClients") or Counter()).items():
                    before = cumulative_bounce_clients.get(client_id, 0)
                    after = before + int(increment)
                    cumulative_bounce_clients[client_id] = after
                    if before < 5 <= after:
                        repeat_bounce_clients5 += 1

                for name in behavior_names:
                    behavior[name] += int(bucket.get(name) or 0)
                for name in technical_names:
                    technical_quality[name] += int(bucket.get(name) or 0)

                if visits < MIN_SOURCE_VISITS:
                    continue

                end_text = dates[end_index].isoformat()
                summary = range_summary(
                    visits=visits,
                    client_visits=client_visits,
                    unique_clients=len(cumulative["clients"]),
                    client_entries=current_top_entries(cumulative["clients"], heaps["clients"], 10),
                    ip_entries=current_top_entries(cumulative["ips"], heaps["ips"], 1),
                    subnet_entries=current_top_entries(cumulative["subnets"], heaps["subnets"], 1),
                    browser_entries=current_top_entries(cumulative["browsers"], heaps["browsers"], 1),
                    profile_entries=current_top_entries(cumulative["profiles"], heaps["profiles"], 1),
                    metrics=cumulative_metrics,
                    repeat_bounce_clients5=repeat_bounce_clients5,
                    behavior=behavior,
                    technical_quality=technical_quality,
                    active_days=active_days,
                )
                payload["ranges"].setdefault(end_text, {})[source] = summary
    return payloads


def build_slice_payloads(
    counter_id: int,
    daily: dict[str, dict[str, dict]],
    generated_at: str,
) -> dict[str, dict]:
    months: dict[str, dict] = {}
    records: dict[str, dict[tuple[str, str, str], dict]] = defaultdict(dict)
    source_month_visits: dict[str, Counter[str]] = defaultdict(Counter)

    for source, source_days in sorted(daily.items()):
        for date_text, bucket in sorted(source_days.items()):
            month = date_text[:7]
            source_month_visits[month][source] += int(bucket.get("visits") or 0)
            for dimension in SLICE_DIMENSIONS:
                for value, stats in (bucket.get("groups", {}).get(dimension) or {}).items():
                    key = (source, dimension, value)
                    record = records[month].setdefault(
                        key,
                        {
                            "source": source,
                            "dimension": dimension,
                            "value": value,
                            "meta": dict(
                                (bucket.get("groupMeta", {}).get(dimension) or {}).get(value) or {}
                            ),
                            "visits": 0,
                            "days": [],
                        },
                    )
                    record["visits"] += int(stats.get("visits") or 0)
                    record["days"].append(
                        [
                            date_text,
                            int(stats.get("visits") or 0),
                            int(stats.get("bounceVisits") or 0),
                            int(stats.get("durationSum") or 0),
                            int(stats.get("pageViewsSum") or 0),
                            int(stats.get("qualityVisits") or 0),
                            int(stats.get("newVisits") or 0),
                        ]
                    )

    for month, month_records in sorted(records.items()):
        groups = []
        dates: list[str] = []
        for record in month_records.values():
            source_visits = int(source_month_visits[month].get(record["source"], 0))
            share = record["visits"] / source_visits if source_visits else 0.0
            if record["visits"] < SLICE_MIN_MONTH_VISITS and share < SLICE_MIN_MONTH_SHARE:
                continue
            record["days"].sort(key=lambda item: item[0])
            dates.extend(item[0] for item in record["days"])
            if not record["meta"]:
                record.pop("meta", None)
            groups.append(record)
        groups.sort(key=lambda item: (item["source"], item["dimension"], -item["visits"], item["value"]))
        months[month] = {
            "version": SLICE_VERSION,
            "counterId": counter_id,
            "month": month,
            "from": min(dates) if dates else f"{month}-01",
            "to": max(dates) if dates else f"{month}-01",
            "generatedAt": generated_at,
            "method": "safe-daily-slices-v1",
            "campaignFilter": list(CAMPAIGN_TOKENS),
            "dayFields": list(SLICE_DAY_FIELDS),
            "retention": {
                "minMonthlyVisits": SLICE_MIN_MONTH_VISITS,
                "minMonthlySourceShare": SLICE_MIN_MONTH_SHARE,
            },
            "privacy": (
                "Referrers are reduced to hostnames. Raw URLs, ClientID, VisitID, IP and subnet "
                "identifiers are not stored."
            ),
            "groups": groups,
        }
    return months


def write_period_payloads(counter_id: int, payloads: dict[str, dict]) -> None:
    output_dir = DATA_DIR / str(counter_id) / "clientid-periods"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for start_text, payload in sorted(payloads.items()):
        write_compact_json(output_dir / f"{start_text}.json", payload)


def write_slice_payloads(counter_id: int, payloads: dict[str, dict]) -> None:
    output_dir = DATA_DIR / str(counter_id) / "slices"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for month, payload in sorted(payloads.items()):
        write_compact_json(output_dir / f"{month}.json", payload)


def patch_catalog(
    counter_id: int,
    start: dt.date,
    end: dt.date,
    generated_at: str,
    slice_months: list[str],
) -> None:
    catalog = read_json(CATALOG_PATH, {})
    if not isinstance(catalog, dict):
        raise RuntimeError("Fraud catalog is unavailable or invalid")
    found = False
    period_info = {
        "version": PERIOD_VERSION,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "maxDays": MAX_PERIOD_DAYS,
        "pathTemplate": f"{counter_id}/clientid-periods/{{from}}.json",
        "method": "exact-period-metrics-v3",
    }
    slice_info = {
        "version": SLICE_VERSION,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "maxDays": MAX_PERIOD_DAYS,
        "pathTemplate": f"{counter_id}/slices/{{month}}.json",
        "method": "safe-daily-slices-v1",
        "months": slice_months,
        "dimensions": list(SLICE_DIMENSIONS),
    }
    for counter in catalog.get("counters") or []:
        if int(counter.get("id") or 0) != counter_id:
            continue
        counter["periodMetrics"] = dict(period_info)
        counter["clientIdPeriods"] = dict(period_info)
        counter["sliceMetrics"] = dict(slice_info)
        found = True
        break
    if not found:
        raise RuntimeError(f"Counter {counter_id} is missing from fraud catalog")
    catalog["periodMetricsGeneratedAt"] = generated_at
    catalog["periodMetricsModel"] = "exact-period-metrics-v3"
    catalog["clientIdPeriodsGeneratedAt"] = generated_at
    catalog["clientIdPeriodModel"] = "exact-period-metrics-v3"
    catalog["sliceMetricsGeneratedAt"] = generated_at
    catalog["sliceMetricsModel"] = "safe-daily-slices-v1"
    write_json(CATALOG_PATH, catalog)


def self_test() -> None:
    start = dt.date(2026, 7, 1)
    end = dt.date(2026, 7, 2)
    daily: dict[str, dict[str, dict]] = defaultdict(dict)
    rows = []
    for index in range(24):
        day = "2026-07-01" if index < 12 else "2026-07-02"
        rows.append(
            {
                "ym:s:date": day,
                "ym:s:dateTimeUTC": f"{day} 10:00:00",
                "ym:s:clientID": "client-a",
                "ym:s:ipAddress": "10.20.30.40",
                "ym:s:lastUTMSource": "MTS",
                "ym:s:lastUTMCampaign": "level_prg_test",
                "ym:s:referer": "https://publisher.example/path?secret=1",
                "ym:s:bounce": "1",
                "ym:s:visitDuration": "2",
                "ym:s:pageViews": "1",
                "ym:s:isNewUser": "1",
                "ym:s:goalsID": "[411053186,123,456]" if index == 0 else "[]",
                "ym:s:goalsDateTime": (
                    f'["{day} 10:00:10","{day} 10:00:11","{day} 10:00:12"]'
                    if index == 0
                    else "[]"
                ),
                "ym:s:browser": "ChromeMobile",
                "ym:s:browserMajorVersion": "149",
                "ym:s:browserMinorVersion": "0",
                "ym:s:operatingSystem": "Android 14",
                "ym:s:deviceCategory": "2",
                "ym:s:mobilePhone": "Google",
                "ym:s:mobilePhoneModel": "Pixel 7",
                "ym:s:screenWidth": "412",
                "ym:s:screenHeight": "892",
                "ym:s:cookieEnabled": "0" if index == 1 else "1",
            }
        )
    for row in rows:
        assert process_period_row(daily, row, quality_goal_id=411053186)

    payloads = build_period_payloads(53197618, start, end, daily, "test")
    result = payloads["2026-07-01"]["ranges"]["2026-07-02"]["mts"]
    assert result["visits"] == 24
    assert result["clientIdVisits"] == 24
    assert result["uniqueClientIds"] == 1
    assert result["repeatBounceClients5"] == 1
    assert result["fastAnyGoal15Visits"] == 1
    assert result["fastQualityGoal15Visits"] == 1
    assert result["multiGoalVisits"] == 1
    assert result["bounce"] == 1.0
    assert result["depth"] == 1.0
    assert result["topIpVisits"] == 24
    assert result["topSubnetVisits"] == 24
    assert result["topBrowser"] == "ChromeMobile 149"
    assert result["topProfileVisits"] == 24

    slices = build_slice_payloads(53197618, daily, "test")
    groups = slices["2026-07"]["groups"]
    referrer = next(item for item in groups if item["dimension"] == "referrer")
    assert referrer["value"] == "publisher.example"
    browser_resolution = next(
        item for item in groups if item["dimension"] == "browserResolution"
    )
    assert browser_resolution["meta"]["resolution"] == "412x892"

    serialized = json.dumps({"periods": payloads, "slices": slices}, ensure_ascii=False)
    assert "client-a" not in serialized
    assert "10.20.30.40" not in serialized
    assert "10.20.30.0/24" not in serialized
    assert "/path" not in serialized
    assert "secret=1" not in serialized
    print("Exact period metrics and safe slice self-test passed")


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
        period_payloads = build_period_payloads(counter_id, start, end, daily, generated_at)
        slice_payloads = build_slice_payloads(counter_id, daily, generated_at)
        write_period_payloads(counter_id, period_payloads)
        write_slice_payloads(counter_id, slice_payloads)
        patch_catalog(counter_id, start, end, generated_at, sorted(slice_payloads))
        print(
            json.dumps(
                {
                    "counterId": counter_id,
                    "periodFiles": len(period_payloads),
                    "sliceFiles": len(slice_payloads),
                    "sources": len(daily),
                    **operation,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
