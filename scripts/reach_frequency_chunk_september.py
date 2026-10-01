#!/usr/bin/env python3
"""September 2026 raw-impression chunk with complete placement audit."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import pickle
import urllib.request
from collections import Counter
from pathlib import Path

from pyroaring import BitMap64

import export_reach_frequency_july_leaf as base


def project_for(placement_name: str) -> tuple[str, str]:
    project, scope = base.classify_project(placement_name)
    if scope != "unassigned":
        return project, scope
    name = placement_name.casefold().replace("ё", "е")
    for marker, label in (
        ("волга", "Level Волга"),
        ("академическая", "Level Академическая"),
        ("бауманская", "Level Бауманская"),
        ("причальный", "Level Причальный"),
    ):
        if marker in name:
            return label, "object"
    return project, scope


def leaf() -> dict:
    return {"impressions": 0, "withDevice": 0, "devices": BitMap64()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--audit-out", required=True)
    args = parser.parse_args()
    token = os.environ["TARGETADS_TOKEN"].strip()
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    meta = base.load_meta(token, project_id)
    classified = {}
    for placement_id, item in meta.items():
        name = item.get("placementName", "")
        project, scope = project_for(name)
        classified[placement_id] = {
            "placementId": placement_id,
            "placementName": name,
            "source": item.get("sourceName", ""),
            "project": project,
            "scope": scope,
        }
    start = dt.date.fromisoformat(args.from_date)
    end = dt.date.fromisoformat(args.to_date)
    if not dt.date(2026, 9, 1) <= start <= end <= dt.date(2026, 9, 30):
        raise ValueError("Chunk must fall within full September 2026")
    job_id = base.create_job(token, project_id, start, end)
    url = base.wait_job(token, project_id, job_id)
    projects = {}
    placement_impressions = Counter()
    total_impressions = total_with_device = unknown_impressions = 0
    request = urllib.request.Request(url, headers={"User-Agent": "LevelReachFrequency/september-complete"})
    with urllib.request.urlopen(request, timeout=300) as response:
        with gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"InteractionDeviceID", "InteractionPlacementId"}
                if not reader.fieldnames or not required.issubset(reader.fieldnames):
                    raise RuntimeError(f"Unexpected Target Ads columns: {reader.fieldnames}")
                for row in reader:
                    total_impressions += 1
                    placement_id = str(row.get("InteractionPlacementId") or "").strip()
                    if placement_id not in meta:
                        unknown_impressions += 1
                    item = classified.get(placement_id)
                    if item is None:
                        item = {
                            "placementId": placement_id,
                            "placementName": f"Placement {placement_id or 'без ID'}",
                            "source": "",
                            "project": "Без объекта",
                            "scope": "unassigned",
                        }
                        classified[placement_id] = item
                    placement_impressions[placement_id] += 1
                    project = projects.get(item["project"])
                    if project is None:
                        project = {"scope": item["scope"], "channels": {}, "unclassified": leaf()}
                        projects[item["project"]] = project
                    bucket = project["unclassified"]
                    bucket["impressions"] += 1
                    device_id = str(row.get("InteractionDeviceID") or "").strip()
                    if device_id:
                        total_with_device += 1
                        bucket["withDevice"] += 1
                        bucket["devices"].add(base.h64(device_id))
    if sum(placement_impressions.values()) != total_impressions:
        raise RuntimeError("Placement audit does not reconcile to raw impressions")
    data = {
        "from": args.from_date,
        "to": args.to_date,
        "jobId": job_id,
        "projectId": project_id,
        "placementMetaCount": len(meta),
        "totalImpressions": total_impressions,
        "totalWithDevice": total_with_device,
        "unknownPlacementImpressions": unknown_impressions,
        "projects": projects,
    }
    with Path(args.out).open("wb") as handle:
        pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)
    audit = {
        "from": args.from_date,
        "to": args.to_date,
        "totalImpressions": total_impressions,
        "placements": [
            {**classified[placement_id], "impressions": impressions}
            for placement_id, impressions in sorted(placement_impressions.items())
        ],
    }
    Path(args.audit_out).write_text(json.dumps(audit, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "from": args.from_date,
        "to": args.to_date,
        "impressions": total_impressions,
        "projects": len(projects),
        "activePlacements": len(placement_impressions),
        "unknownPlacementImpressions": unknown_impressions,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
