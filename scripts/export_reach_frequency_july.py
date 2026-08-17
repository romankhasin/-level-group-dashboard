#!/usr/bin/env python3
"""One-off July 2026 Reach/Frequency export from Target Ads aggregated API."""

from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reach_frequency_july_2026.json"
API_URL = "https://api.targetads.io/v1/reports/agg_report"
DATE_FROM = "2026-07-01"
DATE_TO = "2026-07-31"


def request_report(token: str, project_id: int, fields: list[str]) -> dict:
    url = API_URL + "?" + urllib.parse.urlencode({"project_id": project_id})
    body = {
        "ResponseType": "JSON",
        "Fields": fields,
        "MediaMetrics": ["Impressions", "Reach", "Frequency"],
        "InteractionFilter": {"DateFrom": DATE_FROM, "DateTo": DATE_TO},
        "AttributionModel": "mli",
        "AttributionWindow": "30",
        "DateGrouping": "month",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Target Ads HTTP {exc.code}: {details[:3000]}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected non-object Target Ads response")
    if payload.get("ErrorCode"):
        raise RuntimeError(
            f"Target Ads API error {payload.get('ErrorCode')}: {payload.get('ErrorMessage')} | {payload.get('Errors')}"
        )
    return payload


def normalize_rows(payload: dict) -> list[dict]:
    rows = payload.get("data") or []
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        for key in ("Impressions", "Reach", "Frequency"):
            value = item.get(key)
            try:
                item[key] = float(value or 0)
            except (TypeError, ValueError):
                pass
        normalized.append(item)
    return normalized


def main() -> None:
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    raw_project_id = os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787"
    project_id = int(raw_project_id)

    by_source = request_report(token, project_id, ["EventDate", "MediaSource"])
    by_campaign = request_report(token, project_id, ["MediaSource", "MediaCampaign"])

    result = {
        "period": {"from": DATE_FROM, "to": DATE_TO},
        "projectId": project_id,
        "note": "Prototype export from Target Ads Aggregated data API. Reach is deduplicated within each returned grouping row; do not sum Reach across rows to obtain a cross-channel total.",
        "bySource": normalize_rows(by_source),
        "byCampaign": normalize_rows(by_campaign),
        "counts": {
            "bySource": int(by_source.get("count") or len(by_source.get("data") or [])),
            "byCampaign": int(by_campaign.get("count") or len(by_campaign.get("data") or [])),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
