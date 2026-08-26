#!/usr/bin/env python3
"""Refresh dashboard data with automatic Target Ads impressions and clicks.

The published verifier history is the normal incremental baseline. On the
first run after restoring automation, the runner bootstraps from the last
published dashboard that still contained automatic Target Ads data (through
2026-08-10), then catches up the missing dates through Moscow yesterday.
Existing source-priority rules stay in ``merge_verifier_rows``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import update_data as base

PRE_DISABLE_LATEST_URL = (
    "https://raw.githubusercontent.com/romankhasin/-level-group-dashboard/"
    "5f5f423f8b7ff656194729fd83df5fdd2a574176/data/latest.json"
)


def read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def targetads_was_automatic(payload: dict) -> bool:
    status = payload.get("status") or {}
    targetads = status.get("targetads") or {}
    return bool(targetads.get("enabled")) and str(targetads.get("mode") or "") != "manual_upload_only"


def load_targetads_baseline(previous_latest: dict) -> tuple[dict, bool]:
    """Return a baseline known to contain Target Ads media history."""
    if targetads_was_automatic(previous_latest):
        return previous_latest, False

    bootstrap_path = base.ROOT / ".cache" / "targetads_pre_disable_latest.json"
    bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
    base.download_file(PRE_DISABLE_LATEST_URL, bootstrap_path)
    bootstrap = read_json_object(bootstrap_path)
    bootstrap_rows = bootstrap.get("verifierRows") or []
    bootstrap_to = parse_date((bootstrap.get("period") or {}).get("to"))
    if not isinstance(bootstrap_rows, list) or not bootstrap_rows or not bootstrap_to:
        raise RuntimeError("Could not load the pre-disable Target Ads dashboard baseline")
    return bootstrap, True


def targetads_incremental_rows(
    token: str,
    yesterday: dt.date,
    previous_latest: dict,
) -> tuple[list[dict], dict]:
    project_id = base.targetads_project_id()
    base.validate_targetads_token(token, project_id)
    placements, creatives = base.fetch_targetads_metadata(token, project_id)

    baseline_latest, bootstrap_used = load_targetads_baseline(previous_latest)
    previous_rows = baseline_latest.get("verifierRows") or []
    if not isinstance(previous_rows, list):
        previous_rows = []

    previous_to = parse_date((baseline_latest.get("period") or {}).get("to"))
    if previous_to and previous_to < yesterday:
        fetch_from = previous_to + dt.timedelta(days=1)
    else:
        # Re-running on the same day should replace, not duplicate, yesterday.
        fetch_from = yesterday
    fetch_from = max(fetch_from, base.START_DATE)

    fresh_rows: list[dict] = []
    jobs: list[dict] = []
    if fetch_from <= yesterday:
        for chunk_start, chunk_end in base.date_chunks(fetch_from, yesterday, days=3):
            chunk_rows, chunk_status = base.fetch_targetads_period(
                token,
                project_id,
                placements,
                creatives,
                chunk_start,
                chunk_end,
            )
            fresh_rows.extend(chunk_rows)
            jobs.extend(chunk_status.get("jobs") or [])

    # Drop only dates being refreshed. Older final verifier rows remain the
    # baseline; current Google/Avito facts are applied again below and keep
    # their priority wherever the agreed exceptions require it.
    baseline_rows = []
    for row in previous_rows:
        row_date = parse_date(row.get("interaction_dt")) if isinstance(row, dict) else None
        if row_date and fetch_from <= row_date <= yesterday:
            continue
        if isinstance(row, dict):
            baseline_rows.append(row)

    targetads_status = {
        "enabled": True,
        "configured": True,
        "mode": "automatic_raw_v2_incremental",
        "api": "raw_data_v2",
        "project_id": project_id,
        "token_valid": True,
        "campaigns_available": len(placements),
        "creatives_available": len(creatives),
        "identity_field": "creative_name",
        "bootstrap_used": bootstrap_used,
        "bootstrap_through": previous_to.isoformat() if bootstrap_used and previous_to else None,
        "from": fetch_from.isoformat(),
        "to": yesterday.isoformat(),
        "as_of": yesterday.isoformat(),
        "media_metrics": ["impressions", "clicks"],
        "new_rows": len(fresh_rows),
        "baseline_rows": len(baseline_rows),
        "jobs_completed": len(jobs),
        "events_aggregated": sum(int(job.get("aggregated_events") or 0) for job in jobs),
        "warning": None if placements and creatives else "Target Ads metadata is incomplete",
    }
    return [*baseline_rows, *fresh_rows], targetads_status


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
    previous_latest = read_json_object(base.LATEST_PATH)

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
    targetads_rows, targetads_status = targetads_incremental_rows(
        targetads_token,
        yesterday,
        previous_latest,
    )

    # Existing merge rules enforce the agreed exceptions:
    # Yandex/MTS PRG -> Google when media facts exist;
    # Avito MED/MRK -> Google/Avito when media facts exist;
    # all other campaigns -> Target Ads impressions/clicks.
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
                "targetAdsMode": "automatic_raw_v2_incremental",
                "targetAdsBootstrap": targetads_status["bootstrap_used"],
                "targetAdsFetchFrom": targetads_status["from"],
                "targetAdsNewRows": targetads_status["new_rows"],
                "targetAdsJobs": targetads_status["jobs_completed"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
