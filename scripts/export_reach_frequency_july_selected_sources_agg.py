#!/usr/bin/env python3
"""July 2026 source reach for six projects via Target Ads historical aggregate API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import export_reach_frequency_july_leaf as targetads
from export_reach_frequency_july_selected_sources import PROJECTS, SOURCES, normalized, project_of, source_of
from test_targetads_agg_ivt import normalize_rows


API = "https://api.targetads.io/v1/reports/agg_report"
PERIOD = {"from": "2026-07-01", "to": "2026-07-31"}
OUT = Path("data/reach_frequency_july_2026_selected_sources.json")


def query(token: str, project_id: int, fields: list[str], metrics: list[str], placement_ids: list[int] | None = None) -> list[dict]:
    interaction_filter = {"DateFrom": PERIOD["from"], "DateTo": PERIOD["to"]}
    if placement_ids is not None:
        if not 1 <= len(placement_ids) <= 20:
            raise ValueError(f"Placement filter size must be 1..20, got {len(placement_ids)}")
        interaction_filter["MediaCampaignId"] = placement_ids
    payload = {
        "ResponseType": "JSON", "Fields": fields, "MediaMetrics": metrics,
        "InteractionFilter": interaction_filter, "AttributionModel": "mli",
        "AttributionWindow": "30", "DateGrouping": "month", "Limit": 100000,
    }
    url = API + "?" + urllib.parse.urlencode({"project_id": project_id})
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Target Ads HTTP {error.code}: {details[:2000]}") from error
    if not isinstance(result, dict) or result.get("ErrorCode"):
        raise RuntimeError(f"Unexpected Target Ads response: {str(result)[:2000]}")
    rows, _, _ = normalize_rows(result)
    count = result.get("count", result.get("CountRows"))
    if count is not None and int(count) > len(rows):
        raise RuntimeError(f"Truncated aggregate response: {count} total rows, {len(rows)} received")
    return rows


def integer(row: dict, key: str) -> int:
    value = Decimal(str(row.get(key, "0") or "0"))
    if value < 0 or value != value.to_integral_value():
        raise RuntimeError(f"Invalid {key}: {row}")
    return int(value)


def main() -> None:
    token = os.environ["TARGETADS_TOKEN"].strip()
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    meta = targetads.load_meta(token, project_id)
    ids_by_name = defaultdict(list)
    for placement_id, item in meta.items():
        name = normalized(str(item.get("placementName") or ""))
        source = source_of(str(item.get("sourceName") or ""))
        if source:
            ids_by_name[(source, name)].append(int(placement_id))

    discovery = query(token, project_id, ["MediaSource", "MediaCampaign"], ["Impressions"])
    active_ids = defaultdict(set)
    expected_impressions = defaultdict(int)
    matched_campaigns = defaultdict(list)
    unmatched = []
    for row in discovery:
        source = source_of(str(row.get("MediaSource") or ""))
        campaign = str(row.get("MediaCampaign") or "")
        project = project_of(campaign)
        impressions = integer(row, "Impressions")
        if not source or not project or impressions == 0:
            continue
        ids = ids_by_name.get((source, normalized(campaign)), [])
        if not ids:
            unmatched.append({"source": source, "campaign": campaign, "project": project, "impressions": impressions})
            continue
        active_ids[source].update(ids)
        expected_impressions[source] += impressions
        matched_campaigns[source].append({"campaign": campaign, "project": project, "impressions": impressions, "placementIds": ids})
    if unmatched:
        raise RuntimeError(f"Active campaigns absent from metadata: {unmatched[:10]}")
    project_coverage = {project for campaigns in matched_campaigns.values() for item in campaigns for project in [item["project"]]}
    if project_coverage != set(PROJECTS):
        raise RuntimeError(f"Missing selected projects: {sorted(set(PROJECTS) - project_coverage)}")

    output_rows = []
    for source in SOURCES:
        ids = sorted(active_ids[source])
        if not ids:
            output_rows.append({"source": source, "impressions": 0, "reach": 0, "frequency": None, "placementIds": [], "campaigns": []})
            continue
        if len(ids) > 20:
            raise RuntimeError(f"{source} has {len(ids)} active/duplicate placement IDs, exceeding the exact-filter limit")
        measured = query(token, project_id, ["EventDate", "MediaSource"], ["Impressions", "Reach", "Frequency"], ids)
        positive = [row for row in measured if integer(row, "Impressions") > 0]
        if len(positive) != 1 or source_of(str(positive[0].get("MediaSource") or "")) != source:
            raise RuntimeError(f"Expected one monthly row for {source}, got {positive[:5]}")
        impressions = integer(positive[0], "Impressions")
        reach = integer(positive[0], "Reach")
        if impressions != expected_impressions[source]:
            raise RuntimeError(f"{source}: filtered impressions {impressions} != discovered campaigns {expected_impressions[source]}")
        if reach > impressions:
            raise RuntimeError(f"{source}: reach exceeds impressions")
        output_rows.append({
            "source": source, "impressions": impressions, "reach": reach,
            "frequency": round(impressions / reach, 4) if reach else None,
            "apiFrequency": positive[0].get("Frequency"),
            "placementIds": ids, "campaigns": matched_campaigns[source],
        })
        print(json.dumps({"source": source, "impressions": impressions, "reach": reach, "placementCount": len(ids)}, ensure_ascii=False), flush=True)

    result = {
        "period": PERIOD, "projectId": project_id, "projects": list(PROJECTS),
        "method": "Target Ads Aggregated data API: monthly source-level Reach filtered to all active placements of the six projects; frequency = impressions / Reach",
        "notes": "Regional variants of the six projects included; Work Нижегородская excluded. Monthly Reach is queried jointly for each source, not summed across campaigns.",
        "sourceRows": output_rows, "discoveryRowCount": len(discovery),
        "projectCoverage": sorted(project_coverage),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "success", "sources": len(output_rows), "output": str(OUT)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
