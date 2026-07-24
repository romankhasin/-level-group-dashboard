#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COUNTER_ID = 53197618
DATE_FROM = "2026-05-01"
DATE_TO = "2026-06-30"
FILTER = "ym:s:lastsignUTMCampaign=@'prg'"
OUT_DIR = Path("antifraud_prg_53197618/raw")
QUALITY_GOAL_ID = 411053186


def request_json(url: str, token: str, attempts: int = 5) -> dict:
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"OAuth {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:3000]
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                raise RuntimeError(f"HTTP {exc.code}: {details}\nURL: {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise RuntimeError(f"Request failed: {exc}\nURL: {url}") from exc
        time.sleep(min(2**attempt, 20))
    raise RuntimeError("Request failed")


def dim_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or "").strip()
    return str(value or "").strip()


def safe_rate(value: object) -> float:
    number = float(value or 0)
    return number / 100.0 if number > 1 else number


def find_goals(token: str) -> tuple[int, int, list[dict]]:
    url = f"https://api-metrika.yandex.net/management/v1/counter/{COUNTER_ID}/goals"
    payload = request_json(url, token)
    goals = payload.get("goals") or []

    quality_id = QUALITY_GOAL_ID
    primary_id = 0
    for goal in goals:
        name = str(goal.get("name") or "").strip().lower().replace("ё", "е")
        goal_id = int(goal.get("id") or 0)
        if "первично-качествен" in name and "звон" in name:
            quality_id = goal_id
        if "первичн" in name and "звон" in name and "качествен" not in name:
            primary_id = goal_id

    if not primary_id:
        candidates = [
            {"id": int(g.get("id") or 0), "name": str(g.get("name") or "")}
            for g in goals
            if "звон" in str(g.get("name") or "").lower()
        ]
        raise RuntimeError(
            "Не найдена цель 'Первичные звонки UIS'. Доступные цели со словом звон: "
            + json.dumps(candidates, ensure_ascii=False)
        )
    return quality_id, primary_id, goals


def build_metrics(quality_id: int, primary_id: int) -> list[str]:
    return [
        "ym:s:visits",
        "ym:s:users",
        "ym:s:bounceRate",
        "ym:s:avgVisitDurationSeconds",
        "ym:s:newUsers",
        f"ym:s:goal{quality_id}conversionRate",
        f"ym:s:goal{primary_id}conversionRate",
    ]


def fetch_report(token: str, dimensions: list[str], metrics: list[str]) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    offset = 1
    limit = 100000
    last_payload: dict = {}
    while True:
        params = {
            "ids": str(COUNTER_ID),
            "date1": DATE_FROM,
            "date2": DATE_TO,
            "metrics": ",".join(metrics),
            "filters": FILTER,
            "accuracy": "full",
            "include_undefined": "true",
            "limit": str(limit),
            "offset": str(offset),
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        url = "https://api-metrika.yandex.net/stat/v1/data?" + urllib.parse.urlencode(params)
        payload = request_json(url, token)
        last_payload = payload
        page = payload.get("data") or []
        for item in page:
            raw_dims = item.get("dimensions") or []
            raw_metrics = item.get("metrics") or []
            rows.append(
                {
                    "dimensions": [dim_text(v) or "Не определено" for v in raw_dims],
                    "metrics": [float(v or 0) for v in raw_metrics],
                }
            )
        total_rows = int(payload.get("total_rows") or len(page))
        if not page or offset - 1 + len(page) >= total_rows:
            break
        offset += len(page)
    return rows, last_payload


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def metric_values(values: list[float]) -> list[object]:
    visits = int(round(values[0] if len(values) > 0 else 0))
    users = int(round(values[1] if len(values) > 1 else 0))
    bounce = safe_rate(values[2] if len(values) > 2 else 0)
    duration = format_duration(values[3] if len(values) > 3 else 0)
    new_users = float(values[4] if len(values) > 4 else 0)
    new_share = new_users / users if users else 0.0
    quality = safe_rate(values[5] if len(values) > 5 else 0)
    primary = safe_rate(values[6] if len(values) > 6 else 0)
    return [visits, users, bounce, duration, new_share, quality, primary]


def write_csv(path: Path, headers: list[str], dimension_count: int, rows: list[dict], totals: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(headers)
        writer.writerow(["Итого и средние", *([""] * (dimension_count - 1)), *metric_values(totals)])
        for row in rows:
            writer.writerow([*row["dimensions"], *metric_values(row["metrics"])])


def main() -> None:
    token = os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("YANDEX_METRIKA_TOKEN is not configured")

    quality_id, primary_id, goals = find_goals(token)
    metrics = build_metrics(quality_id, primary_id)
    total_rows, total_payload = fetch_report(token, [], metrics)
    if not total_rows:
        raise RuntimeError("Итоговый запрос вернул пустой результат")
    totals = total_rows[0]["metrics"]

    tech_dims = [
        "ym:s:lastsignUTMSource",
        "ym:s:operatingSystem",
        "ym:s:browserAndVersionMajor",
        "ym:s:mobilePhoneModel",
        "ym:s:screenResolution",
    ]
    ip_dims = ["ym:s:lastsignUTMSource", "ym:s:ipAddress"]
    tech_rows, tech_payload = fetch_report(token, tech_dims, metrics)
    ip_rows, ip_payload = fetch_report(token, ip_dims, metrics)

    tech_headers = [
        "UTM Source",
        "Операционная система (детально)",
        "Версия браузера",
        "Модель устройства",
        "Разрешение",
        "Визиты",
        "Посетители",
        "Отказы",
        "Время на сайте",
        "Доля новых посетителей",
        "Конверсия (Первично-качественные звонки UIS)",
        "Конверсия (Первичные звонки UIS)",
    ]
    ip_headers = [
        "UTM Source",
        "IP-адрес",
        "Визиты",
        "Посетители",
        "Отказы",
        "Время на сайте",
        "Доля новых посетителей",
        "Конверсия (Первично-качественные звонки UIS)",
        "Конверсия (Первичные звонки UIS)",
    ]
    write_csv(OUT_DIR / "tech.csv", tech_headers, len(tech_dims), tech_rows, totals)
    write_csv(OUT_DIR / "ip.csv", ip_headers, len(ip_dims), ip_rows, totals)

    metadata = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "counter_id": COUNTER_ID,
        "period": {"from": DATE_FROM, "to": DATE_TO},
        "filter": "UTM Campaign содержит prg",
        "api_filter": FILTER,
        "goals": {
            "quality": {"id": quality_id, "name": "Первично-качественные звонки UIS"},
            "primary": {"id": primary_id, "name": "Первичные звонки UIS"},
        },
        "totals": metric_values(totals),
        "rows": {"tech": len(tech_rows), "ip": len(ip_rows)},
        "total_rows_api": {
            "tech": int(tech_payload.get("total_rows") or len(tech_rows)),
            "ip": int(ip_payload.get("total_rows") or len(ip_rows)),
        },
        "sampled": {
            "total": bool(total_payload.get("sampled")),
            "tech": bool(tech_payload.get("sampled")),
            "ip": bool(ip_payload.get("sampled")),
        },
        "available_call_goals": [
            {"id": int(g.get("id") or 0), "name": str(g.get("name") or "")}
            for g in goals
            if "звон" in str(g.get("name") or "").lower()
        ],
    }
    (OUT_DIR / "meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
