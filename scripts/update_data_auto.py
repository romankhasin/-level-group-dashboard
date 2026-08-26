#!/usr/bin/env python3
"""Refresh dashboard data with automatic Target Ads impressions and clicks.

This runner intentionally reuses the source parsers and merge rules from
``scripts/update_data.py`` while restoring Target Ads Raw Data API v2 as an
automatic source. Google/Avito source priority remains governed by
``merge_verifier_rows`` in the base module.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import update_data as base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-metrika", action="store_true", help="Local validation only")
    parser.add_argument("--workbook", type=Path, help="Use a local workbook instead of downloading")
    args = parser.parse_args()

    now_utc = dt.datetime.now(dt.timezone.utc)
    yesterday = (
        now_utc.astimezone(dt.timezone(dt.timedelta(hours=3))).date()
        - dt.timedelta(days=1)
    )
    if yesterday < base.START_DATE:
        raise RuntimeError("Yesterday is earlier than the configured report start date")

    base.DATA_DIR.mkdir(parents=True, exist_ok=True)

    metrika_token = os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
    if args.skip_metrika:
        metrika_rows = base.read_json_rows(base.METRIKA_HISTORY_PATH)
        metrika_status = {"skipped": True, "total_rows": len(metrika_rows)}
    else:
        if not metrika_token:
            raise RuntimeError("YANDEX_METRIKA_TOKEN is not configured")
        metrika_rows, metrika_status = base.update_metrika(metrika_token, yesterday)

    workbook_path = args.workbook or (base.ROOT / ".cache" / "level_group_report.xlsx")
    if args.workbook is None:
        base.download_file(base.GOOGLE_WORKBOOK_URL, workbook_path)
    live_google_rows, journal_rows, google_status = base.read_google_workbook(
        workbook_path, yesterday
    )

    avito_workbook_path = base.ROOT / ".cache" / "avito_media_report.xlsx"
    base.download_file(base.AVITO_GOOGLE_WORKBOOK_URL, avito_workbook_path)
    avito_google_rows, avito_status = base.read_avito_workbook(
        avito_workbook_path, yesterday
    )

    august_prg_workbook_path = base.ROOT / ".cache" / "august_prg_media_report.xlsx"
    base.download_file(base.AUGUST_PRG_GOOGLE_WORKBOOK_URL, august_prg_workbook_path)
    august_prg_google_rows, august_prg_status = base.read_august_prg_workbook(
        august_prg_workbook_path, yesterday
    )
    august_avito_google_rows, august_avito_status = base.read_august_avito_workbook(
        august_prg_workbook_path, yesterday
    )

    archived_google_rows = base.read_json_rows(base.GOOGLE_ARCHIVE_PATH)
    google_rows = base.merge_google_rows(
        archived_google_rows,
        [
            *live_google_rows,
            *avito_google_rows,
            *august_prg_google_rows,
            *august_avito_google_rows,
        ],
    )
    google_status.update(
        {
            "archive_rows": len(archived_google_rows),
            "live_rows": len(live_google_rows),
            "avito": avito_status,
            "august_avito": august_avito_status,
            "august_prg": august_prg_status,
            "metric_rows": len(google_rows),
        }
    )

    targetads_token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not targetads_token:
        raise RuntimeError("TARGETADS_TOKEN is not configured")
    targetads_rows, targetads_status = base.update_targetads(
        targetads_token, yesterday
    )
    targetads_status.update(
        {
            "enabled": True,
            "mode": "automatic_raw_v2",
            "as_of": yesterday.isoformat(),
            "media_metrics": ["impressions", "clicks"],
        }
    )

    # Source priority is intentionally preserved:
    # - Yandex/MTS PRG: Google facts win when present;
    # - Avito MED/MRK: Google/Avito facts win when present;
    # - everything else: Target Ads supplies impressions/clicks.
    verifier_rows = base.merge_verifier_rows(targetads_rows, google_rows)

    generated_at = now_utc.isoformat().replace("+00:00", "Z")
    latest = {
        "version": 1,
        "generatedAt": generated_at,
        "period": {"from": base.START_DATE.isoformat(), "to": yesterday.isoformat()},
        "rawRows": metrika_rows,
        "verifierRows": verifier_rows,
        "sourceFile": "Автоматическая выгрузка Яндекс Метрики",
        "verifierFile": (
            "Target Ads Raw Data API v2 + Google Данные_метрика + "
            "августовский PRG Google-отчёт + Avito Данные + фиксированный архив июня"
        ),
        "status": {
            "metrika": metrika_status,
            "google": google_status,
            "targetads": targetads_status,
        },
    }
    base.write_json(base.LATEST_PATH, latest)
    base.write_json(base.LATEST_SUMMARY_PATH, base.build_startup_summary(latest))
    base.write_json(
        base.STATUS_PATH,
        {"generatedAt": generated_at, **latest["status"]},
    )
    base.JOURNAL_PATH.write_text(
        base.journal_html(journal_rows, generated_at), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "generatedAt": generated_at,
                "periodTo": yesterday.isoformat(),
                "metrikaRows": len(metrika_rows),
                "verifierRows": len(verifier_rows),
                "journalRows": len(journal_rows),
                "targetAdsEnabled": True,
                "targetAdsMode": "automatic_raw_v2",
                "targetAdsRows": len(targetads_rows),
                "targetAdsNewRows": targetads_status.get("new_rows", 0),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
