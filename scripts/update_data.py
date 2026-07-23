#!/usr/bin/env python3
"""Refresh Level Group dashboard data for GitHub Pages."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
METRIKA_HISTORY_PATH = DATA_DIR / "metrika.json"
TARGETADS_HISTORY_PATH = DATA_DIR / "targetads.json"
LATEST_PATH = DATA_DIR / "latest.json"
STATUS_PATH = DATA_DIR / "status.json"
GOOGLE_ARCHIVE_PATH = DATA_DIR / "google_archive.json"
JOURNAL_PATH = ROOT / "journal.html"

START_DATE = dt.date(2026, 5, 1)
COUNTER_IDS = (53197618, 100470605)
GOOGLE_WORKBOOK_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1chd_Rr_pZPZM83xfI2vx6gziNdLGRmdS/export?format=xlsx"
)
TARGETADS_PROJECT_ID = 12787
GOOGLE_ACTUAL_COST_VAT_MULTIPLIER = 1.22


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    attempts: int = 4,
) -> dict:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=payload,
            headers=request_headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:1000]
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                raise RuntimeError(f"HTTP {error.code} for {url}: {details}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == attempts:
                raise RuntimeError(f"Request failed for {url}: {error}") from error
        time.sleep(min(2**attempt, 15))

    raise RuntimeError(f"Request failed for {url}")


def download_file(url: str, destination: Path, attempts: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LevelGroupDashboard/1.0"})
            with urllib.request.urlopen(request, timeout=240) as response:
                destination.write_bytes(response.read())
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            if attempt == attempts:
                raise RuntimeError(f"Could not download {url}: {error}") from error
            time.sleep(min(2**attempt, 15))


def read_json_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("rows", [])
    return payload if isinstance(payload, list) else []


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def date_chunks(start: dt.date, end: dt.date, days: int = 31):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + dt.timedelta(days=days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + dt.timedelta(days=1)


def dimension_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or "").strip()
    return str(value or "").strip()


def fetch_metrika_period(
    token: str,
    counter_id: int,
    start: dt.date,
    end: dt.date,
) -> list[dict]:
    rows: list[dict] = []
    offset = 1
    limit = 100_000

    while True:
        params = {
            "ids": str(counter_id),
            "date1": start.isoformat(),
            "date2": end.isoformat(),
            "dimensions": "ym:s:date,ym:s:lastsignUTMCampaign",
            "metrics": "ym:s:visits,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
            "accuracy": "full",
            "limit": str(limit),
            "offset": str(offset),
        }
        url = "https://api-metrika.yandex.net/stat/v1/data?" + urllib.parse.urlencode(params)
        payload = request_json(url, headers={"Authorization": f"OAuth {token}"})
        page = payload.get("data") or []

        for item in page:
            dimensions = item.get("dimensions") or []
            metrics = item.get("metrics") or []
            if len(dimensions) < 2 or len(metrics) < 3:
                continue
            report_date = dimension_text(dimensions[0])
            campaign = dimension_text(dimensions[1])
            visits = int(round(float(metrics[0] or 0)))
            if not report_date or not campaign or visits <= 0:
                continue
            rows.append(
                {
                    "counter_id": counter_id,
                    "Дата визита": report_date,
                    "UTM Campaign": campaign,
                    "Визиты": visits,
                    "Отказы": float(metrics[1] or 0),
                    "Время на сайте": float(metrics[2] or 0),
                }
            )

        total_rows = int(payload.get("total_rows") or len(page))
        if not page or offset - 1 + len(page) >= total_rows:
            break
        offset += len(page)

    return rows


def update_metrika(token: str, yesterday: dt.date) -> tuple[list[dict], dict]:
    existing = read_json_rows(METRIKA_HISTORY_PATH)
    keyed: dict[tuple[int, str, str], dict] = {}
    for row in existing:
        try:
            key = (
                int(row.get("counter_id") or 0),
                str(row.get("Дата визита") or ""),
                str(row.get("UTM Campaign") or ""),
            )
        except (TypeError, ValueError):
            continue
        if key[0] and key[1] and key[2]:
            keyed[key] = row

    fetched_count = 0
    ranges: dict[str, dict[str, str | int]] = {}
    for counter_id in COUNTER_IDS:
        counter_dates = [
            dt.date.fromisoformat(key[1])
            for key in keyed
            if key[0] == counter_id and key[1]
        ]
        fetch_from = max(counter_dates) + dt.timedelta(days=1) if counter_dates else START_DATE
        ranges[str(counter_id)] = {
            "from": fetch_from.isoformat(),
            "to": yesterday.isoformat(),
            "new_rows": 0,
        }
        if fetch_from > yesterday:
            continue

        for chunk_start, chunk_end in date_chunks(fetch_from, yesterday):
            new_rows = fetch_metrika_period(token, counter_id, chunk_start, chunk_end)
            for row in new_rows:
                key = (counter_id, row["Дата визита"], row["UTM Campaign"])
                keyed[key] = row
            fetched_count += len(new_rows)
            ranges[str(counter_id)]["new_rows"] += len(new_rows)

    rows = sorted(
        keyed.values(),
        key=lambda row: (
            str(row.get("Дата визита") or ""),
            int(row.get("counter_id") or 0),
            str(row.get("UTM Campaign") or ""),
        ),
    )
    write_json(METRIKA_HISTORY_PATH, {"rows": rows})
    return rows, {"new_rows": fetched_count, "total_rows": len(rows), "ranges": ranges}


def cell_date(value: object) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(text[:10], pattern).date()
        except ValueError:
            pass
    return None


def number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def workbook_rows(sheet) -> tuple[list[str], list[tuple]]:
    iterator = sheet.iter_rows(values_only=True)
    headers = [str(value or "").strip() for value in next(iterator)]
    return headers, list(iterator)


def read_google_workbook(path: Path, yesterday: dt.date) -> tuple[list[dict], list[dict], dict]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    required_sheets = {"Данные_метрика", "Дневник оптимизации"}
    missing = required_sheets.difference(workbook.sheetnames)
    if missing:
        raise RuntimeError(f"Google workbook is missing sheets: {sorted(missing)}")

    metric_headers, metric_source_rows = workbook_rows(workbook["Данные_метрика"])
    metric_index = {name: index for index, name in enumerate(metric_headers)}
    required_metric_headers = {
        "report_date",
        "utm_source",
        "technical_mark",
        "impressions",
        "clicks",
        "cost",
    }
    missing_headers = required_metric_headers.difference(metric_index)
    if missing_headers:
        raise RuntimeError(f"Данные_метрика is missing columns: {sorted(missing_headers)}")

    aggregated: dict[tuple[str, str], dict] = {}
    for row in metric_source_rows:
        source = str(row[metric_index["utm_source"]] or "").strip().lower()
        if source not in {"yandex", "mts"}:
            continue
        report_date = cell_date(row[metric_index["report_date"]])
        technical_mark = str(row[metric_index["technical_mark"]] or "").strip()
        if not report_date or report_date < START_DATE or report_date > yesterday or not technical_mark:
            continue
        key = (report_date.isoformat(), technical_mark.lower())
        if key not in aggregated:
            aggregated[key] = {
                "placement_nm": technical_mark,
                "interaction_dt": report_date.isoformat(),
                "impressions": 0.0,
                "clicks": 0.0,
                "givt": 0.0,
                "fraud_impressions": 0.0,
                "cost": 0.0,
                "has_actual_cost": True,
                "metric_source": source,
            }
        item = aggregated[key]
        item["impressions"] += number(row[metric_index["impressions"]])
        item["clicks"] += number(row[metric_index["clicks"]])
        # The Yandex/MTS source reports actual spend without VAT, while all
        # dashboard plans already include VAT. Add 22% VAT only to actual cost.
        item["cost"] += number(row[metric_index["cost"]]) * GOOGLE_ACTUAL_COST_VAT_MULTIPLIER

    journal_headers, journal_source_rows = workbook_rows(workbook["Дневник оптимизации"])
    expected_journal_headers = [
        "Проект",
        "Канал",
        "Площадка",
        "Период РК",
        "Период оптимизационных действий",
        "Комментарии по оптимизации",
        "Какие действия предпринимаем дальше",
    ]
    if journal_headers[:7] != expected_journal_headers:
        raise RuntimeError("Дневник оптимизации has an unexpected header layout")

    journal_rows = []
    for source_row in journal_source_rows:
        values = list(source_row[:7])
        if not any(value not in (None, "") for value in values):
            continue
        journal_rows.append(
            {header: str(values[index] or "").strip() for index, header in enumerate(expected_journal_headers)}
        )

    workbook.close()
    platforms = sorted({row["Площадка"] for row in journal_rows if row["Площадка"]})
    return (
        sorted(aggregated.values(), key=lambda row: (row["interaction_dt"], row["placement_nm"])),
        journal_rows,
        {
            "metric_rows": len(aggregated),
            "journal_rows": len(journal_rows),
            "journal_platforms": platforms,
        },
    )


def fetch_targetads_period(token: str, start: dt.date, end: dt.date) -> list[dict]:
    url = (
        "https://api.targetads.io/v1/reports/agg_report?"
        + urllib.parse.urlencode({"project_id": TARGETADS_PROJECT_ID})
    )
    body = {
        "ResponseType": "JSON",
        "Fields": ["EventDate", "MediaCampaign"],
        "MediaMetrics": ["Impressions", "Clicks", "GIVT", "SIVT"],
        "InteractionFilter": {"DateFrom": start.isoformat(), "DateTo": end.isoformat()},
        "AttributionModel": "mli",
        "AttributionWindow": "30",
        "DateGrouping": "day",
    }
    payload = request_json(url, headers={"Authorization": f"Bearer {token}"}, body=body)
    source_rows = payload.get("data") or payload.get("Rows") or payload.get("rows") or []
    rows = []
    for item in source_rows:
        if not isinstance(item, dict):
            continue
        campaign = str(item.get("MediaCampaign") or "").strip()
        report_date = cell_date(item.get("EventDate"))
        if not campaign or not report_date:
            continue
        rows.append(
            {
                "placement_nm": campaign,
                "interaction_dt": report_date.isoformat(),
                "impressions": number(item.get("Impressions")),
                "clicks": number(item.get("Clicks")),
                "givt": number(item.get("GIVT")),
                "fraud_impressions": number(item.get("SIVT")),
                "cost": 0.0,
                "has_actual_cost": False,
                "metric_source": "targetads",
            }
        )
    return rows


def update_targetads(token: str, yesterday: dt.date) -> tuple[list[dict], dict]:
    existing = read_json_rows(TARGETADS_HISTORY_PATH)
    keyed = {
        (str(row.get("interaction_dt") or ""), str(row.get("placement_nm") or "").lower()): row
        for row in existing
        if row.get("interaction_dt") and row.get("placement_nm")
    }
    existing_dates = [dt.date.fromisoformat(key[0]) for key in keyed]
    fetch_from = max(existing_dates) + dt.timedelta(days=1) if existing_dates else START_DATE
    fetched_count = 0
    if fetch_from <= yesterday:
        for chunk_start, chunk_end in date_chunks(fetch_from, yesterday, days=31):
            new_rows = fetch_targetads_period(token, chunk_start, chunk_end)
            for row in new_rows:
                keyed[(row["interaction_dt"], row["placement_nm"].lower())] = row
            fetched_count += len(new_rows)

    rows = sorted(keyed.values(), key=lambda row: (row["interaction_dt"], row["placement_nm"]))
    write_json(TARGETADS_HISTORY_PATH, {"rows": rows})
    return rows, {
        "enabled": True,
        "new_rows": fetched_count,
        "total_rows": len(rows),
        "from": fetch_from.isoformat(),
        "to": yesterday.isoformat(),
    }


def merge_verifier_rows(targetads_rows: list[dict], google_rows: list[dict]) -> list[dict]:
    """Merge media facts with Google as a non-empty Yandex/MTS override.

    Target Ads remains the fallback for an exact date + campaign whenever the
    matching Google row is absent or its impressions, clicks, and cost are all
    empty. The merged rows are later parsed into both project and platform
    breakdowns, so the same priority applies at every dashboard level.
    """
    merged: dict[tuple[str, str], dict] = {}
    for row in targetads_rows:
        campaign = str(row.get("placement_nm") or "")
        report_date = str(row.get("interaction_dt") or "")
        if not report_date or not campaign:
            continue
        key = (report_date, campaign.lower())
        merged[key] = row

    for row in google_rows:
        report_date = str(row.get("interaction_dt") or "")
        campaign = str(row.get("placement_nm") or "")
        if not report_date or not campaign:
            continue
        key = (report_date, campaign.lower())
        google_has_media_facts = any(
            number(row.get(metric)) != 0
            for metric in ("impressions", "clicks", "cost")
        )
        if google_has_media_facts or key not in merged:
            merged[key] = row

    return sorted(merged.values(), key=lambda row: (row["interaction_dt"], row["placement_nm"]))


def merge_google_rows(archive_rows: list[dict], live_rows: list[dict]) -> list[dict]:
    """Combine the fixed historical archive with the current Google report.

    Live rows win if a date + technical_mark pair ever exists in both sources.
    """
    merged: dict[tuple[str, str], dict] = {}
    for row in (*archive_rows, *live_rows):
        report_date = str(row.get("interaction_dt") or "").strip()
        technical_mark = str(row.get("placement_nm") or "").strip()
        if not report_date or not technical_mark:
            continue
        merged[(report_date, technical_mark.lower())] = row
    return sorted(merged.values(), key=lambda row: (row["interaction_dt"], row["placement_nm"]))


def journal_html(rows: list[dict], generated_at: str) -> str:
    def text(value: object, fallback: str = "") -> str:
        value_text = str(value or "").strip()
        return value_text or fallback

    def period_label(value: str) -> str:
        try:
            return dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
        except ValueError:
            return value

    def period_sort_key(value: str) -> dt.date:
        try:
            return dt.datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            matches = re.findall(r"(\d{1,2})\.(\d{1,2})", value)
            if matches:
                day, month = matches[-1]
                try:
                    return dt.date(2026, int(month), int(day))
                except ValueError:
                    pass
        return dt.date.min

    period_groups: dict[str, list[dict]] = {}
    for row in rows:
        period = text(row.get("Период оптимизационных действий"), "Без даты")
        period_groups.setdefault(period, []).append(row)

    period_blocks = []
    sorted_periods = sorted(period_groups, key=period_sort_key, reverse=True)
    for period_index, period in enumerate(sorted_periods):
        period_rows = period_groups[period]
        platform_groups: dict[str, list[dict]] = {}
        for row in period_rows:
            platform = text(row.get("Площадка"), "Площадка не указана")
            platform_groups.setdefault(platform, []).append(row)

        platform_blocks = []
        for platform in sorted(platform_groups, key=str.casefold):
            cards = []
            for row in platform_groups[platform]:
                project = html.escape(text(row.get("Проект"), "Без проекта"))
                channel = html.escape(text(row.get("Канал")))
                campaign_period = html.escape(text(row.get("Период РК")))
                comment = html.escape(text(row.get("Комментарии по оптимизации"))).replace("\n", "<br>")
                next_steps = html.escape(text(row.get("Какие действия предпринимаем дальше"))).replace("\n", "<br>")
                meta = " · ".join(value for value in (channel, campaign_period) if value)
                next_block = (
                    f'<div class="next"><strong>Следующие действия</strong><div>{next_steps}</div></div>'
                    if next_steps
                    else ""
                )
                cards.append(
                    f"""
                    <article class="card">
                      <div class="card-head"><h3>{project}</h3><span>{meta}</span></div>
                      <div class="comment">{comment or 'Комментарий не заполнен'}</div>
                      {next_block}
                    </article>
                    """
                )

            platform_blocks.append(
                f"""
                <details class="platform-group">
                  <summary><span>{html.escape(platform)}</span><small>{len(cards)} записей</small></summary>
                  <div class="cards">{''.join(cards)}</div>
                </details>
                """
            )

        open_attr = " open" if period_index == 0 else ""
        period_blocks.append(
            f"""
            <details class="period-group"{open_attr}>
              <summary><span>{html.escape(period_label(period))}</span><small>{len(platform_groups)} площадок · {len(period_rows)} записей</small></summary>
              <div class="platforms">{''.join(platform_blocks)}</div>
            </details>
            """
        )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Журнал оптимизаций — Level Group</title>
  <style>
    :root{{--ink:#20233a;--muted:#747782;--line:#e9e4da;--blue:#ddeef8;--paper:#f7f4ef;--green:#2f7d5a}}
    *{{box-sizing:border-box}} body{{margin:0;font-family:Arial,sans-serif;color:var(--ink);background:linear-gradient(145deg,#fff,var(--paper))}}
    header{{padding:32px 20px;background:linear-gradient(135deg,#fff,var(--blue));border-bottom:1px solid var(--line)}}
    header>div,main{{max-width:1180px;margin:auto}} h1{{margin:0 0 8px;font-size:clamp(28px,5vw,52px)}}
    header p{{margin:0;color:var(--muted);line-height:1.6}} .back{{display:inline-block;margin-top:18px;color:var(--ink);font-weight:700}}
    main{{padding:28px 20px 60px;display:grid;gap:18px}} summary{{cursor:pointer;list-style:none}} summary::-webkit-details-marker{{display:none}}
    .period-group{{background:#fff;border:1px solid var(--line);border-radius:24px;overflow:hidden;box-shadow:0 12px 34px rgba(32,35,58,.06)}}
    .period-group>summary,.platform-group>summary{{display:flex;justify-content:space-between;align-items:center;gap:16px;font-weight:800}}
    .period-group>summary{{padding:22px 24px;font-size:22px;background:linear-gradient(135deg,#fff,var(--blue))}}
    summary small{{color:var(--muted);font-size:13px;font-weight:600}} summary span::before{{content:'›';display:inline-block;margin-right:12px;transition:transform .2s}}
    details[open]>summary span::before{{transform:rotate(90deg)}} .platforms{{padding:14px;display:grid;gap:12px}}
    .platform-group{{border:1px solid var(--line);border-radius:18px;overflow:hidden;background:#fbfaf8}} .platform-group>summary{{padding:17px 18px;font-size:17px}}
    .cards{{padding:0 12px 12px;display:grid;gap:12px}} .card{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:18px}}
    .card-head{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:16px}}
    h3{{margin:0;font-size:18px}} .card-head span{{color:var(--muted);font-size:13px;text-align:right}}
    .comment,.next{{line-height:1.65;font-size:14px}} .next{{margin-top:16px;padding:16px;border-radius:16px;background:#f2f8f4}} .next strong{{display:block;margin-bottom:8px}}
    @media(max-width:700px){{.card-head{{display:block}}.card-head span{{display:block;text-align:left;margin-top:8px}}}}
  </style>
</head>
<body>
  <header><div><h1>Журнал оптимизаций</h1><p>Все площадки · обновлено {html.escape(generated_at)}</p><a class="back" href="./">← Вернуться в дашборд</a></div></header>
  <main>{''.join(period_blocks) if period_blocks else '<p>Записи пока отсутствуют.</p>'}</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-metrika", action="store_true", help="Local validation only")
    parser.add_argument("--workbook", type=Path, help="Use a local workbook instead of downloading")
    args = parser.parse_args()

    now_utc = dt.datetime.now(dt.timezone.utc)
    yesterday = now_utc.astimezone(dt.timezone(dt.timedelta(hours=3))).date() - dt.timedelta(days=1)
    if yesterday < START_DATE:
        raise RuntimeError("Yesterday is earlier than the configured report start date")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
    if args.skip_metrika:
        metrika_rows = read_json_rows(METRIKA_HISTORY_PATH)
        metrika_status = {"skipped": True, "total_rows": len(metrika_rows)}
    else:
        if not token:
            raise RuntimeError("YANDEX_METRIKA_TOKEN is not configured")
        metrika_rows, metrika_status = update_metrika(token, yesterday)

    workbook_path = args.workbook or (ROOT / ".cache" / "level_group_report.xlsx")
    if args.workbook is None:
        download_file(GOOGLE_WORKBOOK_URL, workbook_path)
    live_google_rows, journal_rows, google_status = read_google_workbook(workbook_path, yesterday)
    archived_google_rows = read_json_rows(GOOGLE_ARCHIVE_PATH)
    google_rows = merge_google_rows(archived_google_rows, live_google_rows)
    google_status.update(
        {
            "archive_rows": len(archived_google_rows),
            "live_rows": len(live_google_rows),
            "metric_rows": len(google_rows),
        }
    )

    targetads_token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if targetads_token:
        targetads_rows, targetads_status = update_targetads(targetads_token, yesterday)
    else:
        targetads_rows = read_json_rows(TARGETADS_HISTORY_PATH)
        targetads_status = {
            "enabled": False,
            "message": "TARGETADS_TOKEN is not configured",
            "total_rows": len(targetads_rows),
        }

    verifier_rows = merge_verifier_rows(targetads_rows, google_rows)
    generated_at = now_utc.isoformat().replace("+00:00", "Z")
    latest = {
        "version": 1,
        "generatedAt": generated_at,
        "period": {"from": START_DATE.isoformat(), "to": yesterday.isoformat()},
        "rawRows": metrika_rows,
        "verifierRows": verifier_rows,
        "sourceFile": "Автоматическая выгрузка Яндекс Метрики",
        "verifierFile": "Google Данные_метрика + фиксированный архив июня"
        + (" + Target Ads" if targetads_token else ""),
        "status": {
            "metrika": metrika_status,
            "google": google_status,
            "targetads": targetads_status,
        },
    }
    write_json(LATEST_PATH, latest)
    write_json(STATUS_PATH, {"generatedAt": generated_at, **latest["status"]})
    JOURNAL_PATH.write_text(journal_html(journal_rows, generated_at), encoding="utf-8")

    print(
        json.dumps(
            {
                "generatedAt": generated_at,
                "metrikaRows": len(metrika_rows),
                "verifierRows": len(verifier_rows),
                "journalRows": len(journal_rows),
                "targetAdsEnabled": bool(targetads_token),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
