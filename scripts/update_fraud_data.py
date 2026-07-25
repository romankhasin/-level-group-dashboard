#!/usr/bin/env python3
"""Build privacy-safe daily fraud summaries from Yandex Metrica Logs API."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import time
from collections import Counter
import urllib.error
import urllib.parse
import urllib.request

from fraud_visit_classifier import append_visit_feature, summarize_visit_risk

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "fraud"
CATALOG_PATH = DATA_DIR / "catalog.json"
STATUS_PATH = DATA_DIR / "status.json"
DATA_VERSION = 2

START_DATE = dt.date(2026, 5, 1)
COUNTER_IDS = (53197618, 100470605)
COUNTER_FALLBACK_NAMES = {
    53197618: "Счётчик 53197618",
    100470605: "Счётчик 100470605",
}
QUALITY_CALL_GOAL_IDS = {
    53197618: 411053186,
    100470605: 411053614,
}
REFRESH_DAYS = 3
POLL_INTERVAL_SECONDS = 8
POLL_TIMEOUT_SECONDS = 45 * 60

LOG_FIELDS = [
    "ym:s:visitID",
    "ym:s:date",
    "ym:s:counterUserIDHash",
    "ym:s:clientID",
    "ym:s:ipAddress",
    "ym:s:<attribution>UTMSource",
    "ym:s:bounce",
    "ym:s:visitDuration",
    "ym:s:isNewUser",
    "ym:s:goalsID",
    "ym:s:browser",
    "ym:s:browserMajorVersion",
    "ym:s:browserMinorVersion",
    "ym:s:operatingSystem",
    "ym:s:deviceCategory",
    "ym:s:screenWidth",
    "ym:s:screenHeight",
    "ym:s:cookieEnabled",
]

INVALID_IDS = {"", "0", "undefined", "none", "not set", "(not set)", "не определено"}
UNKNOWN_RE = re.compile(r"не определ|unknown|undefined|other|другие", re.I)
AUTOMATION_RE = re.compile(r"headless|phantom|selenium|webdriver|playwright|puppeteer", re.I)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    attempts: int = 4,
) -> dict:
    headers = {
        "Authorization": f"OAuth {token}",
        "Accept": "application/json",
        "User-Agent": "LevelTrafficFraudLab/0.6",
    }
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=b"" if method == "POST" else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:1500]
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                raise RuntimeError(f"HTTP {error.code} for {url}: {details}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == attempts:
                raise RuntimeError(f"Request failed for {url}: {error}") from error
        time.sleep(min(2**attempt, 20))
    raise RuntimeError(f"Request failed for {url}")


def counter_info(token: str, counter_id: int) -> dict:
    url = f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}"
    try:
        payload = request_json(url, token)
        counter = payload.get("counter") or {}
        return {
            "id": counter_id,
            "name": str(counter.get("name") or COUNTER_FALLBACK_NAMES[counter_id]).strip(),
            "site": str(counter.get("site") or "").strip(),
            "permission": str(counter.get("permission") or "").strip(),
        }
    except RuntimeError:
        return {
            "id": counter_id,
            "name": COUNTER_FALLBACK_NAMES[counter_id],
            "site": "",
            "permission": "",
        }


def create_log_request(token: str, counter_id: int, start: dt.date, end: dt.date) -> int:
    params = {
        "date1": start.isoformat(),
        "date2": end.isoformat(),
        "source": "visits",
        "fields": ",".join(LOG_FIELDS),
        "attribution": "LAST",
    }
    url = (
        f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}/logrequests?"
        + urllib.parse.urlencode(params)
    )
    payload = request_json(url, token, method="POST")
    request_data = payload.get("log_request") or {}
    request_id = int(request_data.get("request_id") or 0)
    if not request_id:
        raise RuntimeError(f"Logs API did not return request_id for counter {counter_id}")
    return request_id


def wait_for_log(token: str, counter_id: int, request_id: int) -> dict:
    url = (
        f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}"
        f"/logrequest/{request_id}"
    )
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last_status = ""
    while time.monotonic() < deadline:
        payload = request_json(url, token)
        log_request = payload.get("log_request") or {}
        status = str(log_request.get("status") or "")
        last_status = status
        if status == "processed":
            return log_request
        if status in {
            "processing_failed",
            "canceled",
            "cleaned_by_user",
            "cleaned_automatically_as_too_old",
        }:
            raise RuntimeError(
                f"Logs request {request_id} for counter {counter_id} ended with status {status}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        f"Logs request {request_id} for counter {counter_id} timed out; last status: {last_status}"
    )


def clean_log_request(token: str, counter_id: int, request_id: int) -> None:
    url = (
        f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}"
        f"/logrequest/{request_id}/clean"
    )
    try:
        request_json(url, token, method="POST", attempts=2)
    except RuntimeError as error:
        print(f"Warning: could not clean Logs request {request_id}: {error}", flush=True)


def field_value(row: dict[str, str], exact: str, suffix: str | None = None) -> str:
    if exact in row:
        return str(row.get(exact) or "")
    if suffix:
        suffix_lower = suffix.lower()
        for key, value in row.items():
            normalized = key.replace("<attribution>", "").lower()
            if normalized.endswith(suffix_lower):
                return str(value or "")
    return ""


def safe_int(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return 0


def valid_id(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in INVALID_IDS else text


def source_name(value: object) -> str:
    text = str(value or "").strip()
    return "Не определено" if text.lower() in INVALID_IDS else text.lower()


def subnet_of(ip: str) -> str:
    value = str(ip or "").strip()
    if ":" in value:
        groups = [part for part in value.split(":") if part][:4]
        return f"{':'.join(groups)}::/64" if groups else "Не определено"
    parts = value.split(".")
    return f"{'.'.join(parts[:3])}.0/24" if len(parts) == 4 else value or "Не определено"


def parse_goals(value: object) -> set[int]:
    return {int(item) for item in re.findall(r"\d+", str(value or ""))}


def top_entry(counter: Counter[str], total: int) -> dict:
    if not counter or total <= 0:
        return {"key": "—", "value": 0, "share": 0.0}
    key, value = counter.most_common(1)[0]
    return {"key": key, "value": value, "share": value / total}


def hidden_top_entry(counter: Counter[str], total: int) -> dict:
    entry = top_entry(counter, total)
    if entry["value"]:
        entry["key"] = "скрыто"
    return entry


def top_share(counter: Counter[str], total: int, limit: int = 10) -> float:
    if not counter or total <= 0:
        return 0.0
    return sum(value for _, value in counter.most_common(limit)) / total


def empty_bucket() -> dict:
    return {
        "visits": 0,
        "user_ids": set(),
        "bounce_sum": 0,
        "duration_sum": 0,
        "new_sum": 0,
        "quality_sum": 0,
        "cookie_sum": 0,
        "ip_counts": Counter(),
        "subnet_counts": Counter(),
        "client_counts": Counter(),
        "browser_counts": Counter(),
        "resolution_counts": Counter(),
        "profile_counts": Counter(),
        "ipv6_visits": 0,
        "unknown_browser_visits": 0,
        "automation": False,
        "visit_features": [],
    }


def process_visit(
    store: dict[tuple[str, str], dict],
    row: dict[str, str],
    *,
    quality_goal_id: int,
) -> None:
    report_date = field_value(row, "ym:s:date").strip()
    if not report_date:
        return
    source = source_name(field_value(row, "ym:s:<attribution>UTMSource", "UTMSource"))
    bucket = store.setdefault((source, report_date), empty_bucket())
    bucket["visits"] += 1

    client_id = valid_id(field_value(row, "ym:s:clientID"))
    user_id = valid_id(field_value(row, "ym:s:counterUserIDHash")) or client_id
    if user_id:
        bucket["user_ids"].add(user_id)

    bucket["bounce_sum"] += 1 if safe_int(field_value(row, "ym:s:bounce")) else 0
    bucket["duration_sum"] += max(0, safe_int(field_value(row, "ym:s:visitDuration")))
    bucket["new_sum"] += 1 if safe_int(field_value(row, "ym:s:isNewUser")) else 0
    bucket["cookie_sum"] += 1 if safe_int(field_value(row, "ym:s:cookieEnabled")) else 0
    if quality_goal_id in parse_goals(field_value(row, "ym:s:goalsID")):
        bucket["quality_sum"] += 1

    ip = valid_id(field_value(row, "ym:s:ipAddress"))
    if ip:
        bucket["ip_counts"][ip] += 1
        bucket["subnet_counts"][subnet_of(ip)] += 1
        if ":" in ip:
            bucket["ipv6_visits"] += 1

    if client_id:
        bucket["client_counts"][client_id] += 1

    browser = field_value(row, "ym:s:browser").strip() or "Не определено"
    major = field_value(row, "ym:s:browserMajorVersion").strip()
    minor = field_value(row, "ym:s:browserMinorVersion").strip()
    version = ".".join(part for part in (major, minor) if part and part != "0")
    browser_version = f"{browser} {version}".strip()
    operating_system = field_value(row, "ym:s:operatingSystem").strip() or "Не определено"
    device = field_value(row, "ym:s:deviceCategory").strip() or "Не определено"
    width = field_value(row, "ym:s:screenWidth").strip()
    height = field_value(row, "ym:s:screenHeight").strip()
    resolution = f"{width}x{height}" if width and height else "Не определено"
    profile = f"{browser_version} · {operating_system} · {device} · {resolution}"

    bucket["browser_counts"][browser_version] += 1
    bucket["resolution_counts"][resolution] += 1
    bucket["profile_counts"][profile] += 1
    if UNKNOWN_RE.search(browser_version):
        bucket["unknown_browser_visits"] += 1
    automation = bool(AUTOMATION_RE.search(browser_version))
    if automation:
        bucket["automation"] = True

    append_visit_feature(
        bucket,
        client_id=client_id,
        ip=ip,
        subnet=subnet_of(ip) if ip else "",
        profile=profile,
        bounce=bool(safe_int(field_value(row, "ym:s:bounce"))),
        duration=max(0, safe_int(field_value(row, "ym:s:visitDuration"))),
        is_new=bool(safe_int(field_value(row, "ym:s:isNewUser"))),
        cookie_enabled=bool(safe_int(field_value(row, "ym:s:cookieEnabled"))),
        quality_goal=quality_goal_id in parse_goals(field_value(row, "ym:s:goalsID")),
        automation=automation,
    )


def download_and_process_parts(
    token: str,
    counter_id: int,
    request_id: int,
    parts: list[dict],
    store: dict[tuple[str, str], dict],
    *,
    quality_goal_id: int,
) -> int:
    processed_rows = 0
    headers = {
        "Authorization": f"OAuth {token}",
        "Accept": "text/tab-separated-values",
        "User-Agent": "LevelTrafficFraudLab/0.6",
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
                text_stream = io.TextIOWrapper(response, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text_stream, delimiter="\t")
                for row in reader:
                    process_visit(store, row, quality_goal_id=quality_goal_id)
                    processed_rows += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Could not download Logs part {part_number} for counter {counter_id}: {error}"
            ) from error
    return processed_rows


def fetch_logs_period(
    token: str,
    counter_id: int,
    start: dt.date,
    end: dt.date,
) -> tuple[list[dict], dict]:
    request_id = create_log_request(token, counter_id, start, end)
    store: dict[tuple[str, str], dict] = {}
    try:
        log_request = wait_for_log(token, counter_id, request_id)
        raw_visits = download_and_process_parts(
            token,
            counter_id,
            request_id,
            list(log_request.get("parts") or []),
            store,
            quality_goal_id=QUALITY_CALL_GOAL_IDS[counter_id],
        )
    finally:
        clean_log_request(token, counter_id, request_id)

    rows = [
        finalize_bucket(source, report_date, bucket)
        for (source, report_date), bucket in store.items()
    ]
    rows.sort(key=lambda item: (item["date"], item["source"]))
    return rows, {
        "requestId": request_id,
        "rawVisits": raw_visits,
        "dailySourceRows": len(rows),
        "from": start.isoformat(),
        "to": end.isoformat(),
    }


def finalize_bucket(source: str, report_date: str, bucket: dict) -> dict:
    visits = int(bucket["visits"])
    users = len(bucket["user_ids"]) or visits
    client_id_visits = sum(bucket["client_counts"].values())
    unique_client_ids = len(bucket["client_counts"])
    top_client = hidden_top_entry(bucket["client_counts"], client_id_visits)
    visit_risk = summarize_visit_risk(bucket, visits)
    metrics = {
        "visits": visits,
        "users": users,
        "bounce": bucket["bounce_sum"] / visits if visits else 0.0,
        "time": bucket["duration_sum"] / visits if visits else 0.0,
        "newShare": bucket["new_sum"] / visits if visits else 0.0,
        "quality": bucket["quality_sum"] / visits if visits else 0.0,
        "primary": 0.0,
    }
    return {
        "source": source,
        "date": report_date,
        "visits": visits,
        "tech": dict(metrics),
        "ip": dict(metrics),
        "metrics": metrics,
        "topBrowser": top_entry(bucket["browser_counts"], visits),
        "topResolution": top_entry(bucket["resolution_counts"], visits),
        "topProfile": top_entry(bucket["profile_counts"], visits),
        "topIp": hidden_top_entry(bucket["ip_counts"], visits),
        "topSubnet": hidden_top_entry(bucket["subnet_counts"], visits),
        "clientIdVisits": client_id_visits,
        "uniqueClientIds": unique_client_ids,
        "topClientId": top_client,
        "top10ClientShare": top_share(bucket["client_counts"], client_id_visits, 10),
        "visitsPerClientId": client_id_visits / unique_client_ids if unique_client_ids else 0.0,
        "repeatClientVisitShare": (
            max(0, client_id_visits - unique_client_ids) / client_id_visits
            if client_id_visits
            else 0.0
        ),
        "clientIdCoverage": client_id_visits / visits if visits else 0.0,
        "ipv6Share": bucket["ipv6_visits"] / visits if visits else 0.0,
        "unknownBrowserShare": bucket["unknown_browser_visits"] / visits if visits else 0.0,
        "cookieEnabledShare": bucket["cookie_sum"] / visits if visits else 0.0,
        "visitRisk": visit_risk,
        "automation": bool(bucket["automation"]),
        "concentrationScope": "daily",
        "dataSource": "yandex-metrica-logs-api",
    }


def month_start(value: dt.date) -> dt.date:
    return value.replace(day=1)


def next_month(value: dt.date) -> dt.date:
    return (value.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def month_end(value: dt.date) -> dt.date:
    return next_month(value) - dt.timedelta(days=1)


def iter_months(start: dt.date, end: dt.date):
    cursor = month_start(start)
    while cursor <= end:
        yield cursor
        cursor = next_month(cursor)


def month_path(counter_id: int, month: dt.date) -> Path:
    return DATA_DIR / str(counter_id) / f"{month:%Y-%m}.json"


def rows_from_month_file(path: Path) -> list[dict]:
    payload = read_json(path, {})
    return list(payload.get("rows") or []) if isinstance(payload, dict) else []


def merge_month_rows(
    existing_rows: list[dict],
    new_rows: list[dict],
    start: dt.date,
    end: dt.date,
) -> list[dict]:
    start_text, end_text = start.isoformat(), end.isoformat()
    keyed = {
        (str(row.get("source") or ""), str(row.get("date") or "")): row
        for row in existing_rows
        if not (start_text <= str(row.get("date") or "") <= end_text)
    }
    for row in new_rows:
        keyed[(str(row.get("source") or ""), str(row.get("date") or ""))] = row
    return sorted(keyed.values(), key=lambda row: (str(row.get("date")), str(row.get("source"))))


def write_month_file(
    path: Path,
    *,
    counter: dict,
    month: dt.date,
    rows: list[dict],
    generated_at: str,
) -> None:
    dates = [str(row.get("date") or "") for row in rows if row.get("date")]
    write_json(
        path,
        {
            "version": DATA_VERSION,
            "counterId": counter["id"],
            "counterName": counter["name"],
            "month": f"{month:%Y-%m}",
            "from": min(dates) if dates else "",
            "to": max(dates) if dates else "",
            "generatedAt": generated_at,
            "source": "Yandex Metrica Logs API",
            "privacy": "Only daily aggregates are stored; raw IP, ClientID and VisitID are discarded.",
            "summary": {
                "visits": sum(int(row.get("visits") or 0) for row in rows),
                "dailySourceRows": len(rows),
                "sources": len({str(row.get("source") or "") for row in rows}),
                "days": len(set(dates)),
            },
            "rows": rows,
        },
    )


def ranges_to_refresh(
    counter_id: int,
    start: dt.date,
    end: dt.date,
    *,
    force: bool,
) -> list[tuple[dt.date, dt.date, dt.date]]:
    refresh_start = max(start, end - dt.timedelta(days=REFRESH_DAYS - 1))
    result = []
    for month in iter_months(start, end):
        available_start = max(start, month)
        available_end = min(end, month_end(month))
        path = month_path(counter_id, month)
        existing_payload = read_json(path, {}) if path.exists() else {}
        existing_version = int(existing_payload.get("version") or 0) if isinstance(existing_payload, dict) else 0
        if force or not path.exists() or existing_version < DATA_VERSION:
            result.append((month, available_start, available_end))
            continue
        overlap_start = max(available_start, refresh_start)
        if overlap_start <= available_end:
            result.append((month, overlap_start, available_end))
    return result


def update_counter(
    token: str,
    counter_id: int,
    start: dt.date,
    end: dt.date,
    *,
    force: bool,
    generated_at: str,
) -> tuple[dict, list[dict]]:
    counter = counter_info(token, counter_id)
    operations = []
    for month, fetch_start, fetch_end in ranges_to_refresh(
        counter_id, start, end, force=force
    ):
        path = month_path(counter_id, month)
        existing_rows = rows_from_month_file(path)
        new_rows, operation = fetch_logs_period(token, counter_id, fetch_start, fetch_end)
        merged_rows = merge_month_rows(existing_rows, new_rows, fetch_start, fetch_end)
        write_month_file(
            path,
            counter=counter,
            month=month,
            rows=merged_rows,
            generated_at=generated_at,
        )
        operation["month"] = f"{month:%Y-%m}"
        operations.append(operation)
        print(
            json.dumps(
                {"counter": counter_id, "month": operation["month"], **operation},
                ensure_ascii=False,
            ),
            flush=True,
        )
    return counter, operations


def build_catalog(counters: list[dict], generated_at: str) -> dict:
    catalog_counters = []
    for counter in counters:
        counter_dir = DATA_DIR / str(counter["id"])
        files = []
        all_dates = []
        total_visits = 0
        if counter_dir.exists():
            for path in sorted(counter_dir.glob("????-??.json")):
                payload = read_json(path, {})
                if not isinstance(payload, dict):
                    continue
                month = str(payload.get("month") or path.stem)
                from_date = str(payload.get("from") or "")
                to_date = str(payload.get("to") or "")
                summary = payload.get("summary") or {}
                total_visits += int(summary.get("visits") or 0)
                if from_date:
                    all_dates.append(from_date)
                if to_date:
                    all_dates.append(to_date)
                files.append(
                    {
                        "month": month,
                        "path": f"{counter['id']}/{path.name}",
                        "from": from_date,
                        "to": to_date,
                        "visits": int(summary.get("visits") or 0),
                    }
                )
        catalog_counters.append(
            {
                **counter,
                "from": min(all_dates) if all_dates else "",
                "to": max(all_dates) if all_dates else "",
                "visits": total_visits,
                "files": files,
            }
        )
    return {
        "version": DATA_VERSION,
        "generatedAt": generated_at,
        "dataThrough": max((item["to"] for item in catalog_counters), default=""),
        "refresh": "daily, previous 3 complete days",
        "privacy": "Public files contain daily aggregates only; raw IP, ClientID and VisitID are never committed.",
        "counters": catalog_counters,
    }


def self_test() -> None:
    rows = [
        {
            "ym:s:date": "2026-07-01",
            "ym:s:counterUserIDHash": "100",
            "ym:s:clientID": "1234567890123456789",
            "ym:s:ipAddress": "10.20.30.40",
            "ym:s:lastUTMSource": "MTS",
            "ym:s:bounce": "0",
            "ym:s:visitDuration": "120",
            "ym:s:isNewUser": "1",
            "ym:s:goalsID": "[411053186]",
            "ym:s:browser": "Chrome",
            "ym:s:browserMajorVersion": "138",
            "ym:s:browserMinorVersion": "0",
            "ym:s:operatingSystem": "Android",
            "ym:s:deviceCategory": "2",
            "ym:s:screenWidth": "360",
            "ym:s:screenHeight": "800",
            "ym:s:cookieEnabled": "1",
        },
        {
            "ym:s:date": "2026-07-01",
            "ym:s:counterUserIDHash": "100",
            "ym:s:clientID": "1234567890123456789",
            "ym:s:ipAddress": "10.20.30.40",
            "ym:s:lastUTMSource": "MTS",
            "ym:s:bounce": "1",
            "ym:s:visitDuration": "0",
            "ym:s:isNewUser": "0",
            "ym:s:goalsID": "[]",
            "ym:s:browser": "Chrome",
            "ym:s:browserMajorVersion": "138",
            "ym:s:browserMinorVersion": "0",
            "ym:s:operatingSystem": "Android",
            "ym:s:deviceCategory": "2",
            "ym:s:screenWidth": "360",
            "ym:s:screenHeight": "800",
            "ym:s:cookieEnabled": "1",
        },
    ]
    store: dict[tuple[str, str], dict] = {}
    for row in rows:
        process_visit(store, row, quality_goal_id=411053186)
    result = finalize_bucket("mts", "2026-07-01", store[("mts", "2026-07-01")])
    assert result["visits"] == 2
    assert result["uniqueClientIds"] == 1
    assert result["clientIdVisits"] == 2
    assert result["topClientId"]["share"] == 1
    assert result["metrics"]["bounce"] == 0.5
    assert result["metrics"]["quality"] == 0.5
    assert result["topIp"]["key"] == "скрыто"
    assert result["visitRisk"]["classifiedVisits"] == 2
    assert result["visitRisk"]["highRiskVisits"] == 0
    assert result["visitRisk"]["reviewVisits"] == 0
    assert "1234567890123456789" not in json.dumps(result)
    print("Fraud Logs API self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counter-id", type=int, action="append")
    parser.add_argument("--date-from", type=dt.date.fromisoformat)
    parser.add_argument("--date-to", type=dt.date.fromisoformat)
    parser.add_argument("--force", action="store_true")
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
    yesterday = today_moscow - dt.timedelta(days=1)
    start = args.date_from or START_DATE
    end = args.date_to or yesterday
    if end >= today_moscow:
        end = yesterday
    if start > end:
        raise RuntimeError(f"Invalid period: {start} — {end}")

    counter_ids = tuple(args.counter_id or COUNTER_IDS)
    unknown = sorted(set(counter_ids).difference(COUNTER_IDS))
    if unknown:
        raise RuntimeError(f"Unsupported counter IDs: {unknown}")

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    counters = []
    statuses = []
    errors = []
    for counter_id in counter_ids:
        try:
            counter, operations = update_counter(
                token,
                counter_id,
                start,
                end,
                force=args.force or args.date_from is not None or args.date_to is not None,
                generated_at=generated_at,
            )
            counters.append(counter)
            statuses.append(
                {
                    "counterId": counter_id,
                    "status": "ok",
                    "operations": operations,
                }
            )
        except Exception as error:  # noqa: BLE001 - status must be written for workflow diagnostics
            errors.append(f"{counter_id}: {error}")
            counters.append(counter_info(token, counter_id))
            statuses.append(
                {
                    "counterId": counter_id,
                    "status": "error",
                    "error": str(error),
                }
            )

    catalog = build_catalog(counters, generated_at)
    write_json(CATALOG_PATH, catalog)
    write_json(
        STATUS_PATH,
        {
            "generatedAt": generated_at,
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "counters": statuses,
            "errors": errors,
        },
    )
    print(
        json.dumps(
            {
                "generatedAt": generated_at,
                "counters": [counter["id"] for counter in counters],
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    if errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    main()
