#!/usr/bin/env python3
"""Merge project-only reach chunks with bounded memory usage."""

import argparse
import gc
import json
import pickle
from pathlib import Path

from pyroaring import BitMap64


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    files = sorted(Path(args.input).rglob("*.pkl"))
    if not files:
        raise RuntimeError("No chunk pickle files found")

    project_scopes = {}
    jobs = []
    total_impressions = 0
    total_with_device = 0
    unknown_placements = 0
    project_id = 12787
    placement_meta_count = 0

    for path in files:
        with path.open("rb") as stream:
            chunk = pickle.load(stream)
        total_impressions += chunk["totalImpressions"]
        total_with_device += chunk["totalWithDevice"]
        unknown_placements += chunk["unknownPlacementImpressions"]
        project_id = chunk["projectId"]
        placement_meta_count = max(placement_meta_count, chunk["placementMetaCount"])
        jobs.append({
            "from": chunk["from"],
            "to": chunk["to"],
            "jobId": chunk["jobId"],
            "rows": chunk["totalImpressions"],
            "rowsWithDevice": chunk["totalWithDevice"],
        })
        for name, project in chunk["projects"].items():
            project_scopes.setdefault(name, project["scope"])
        del chunk

    rows = []
    project_items = list(project_scopes.items())
    batch_size = 5
    for batch_start in range(0, len(project_items), batch_size):
        batch = project_items[batch_start:batch_start + batch_size]
        accumulators = {
            name: {"scope": scope, "devices": BitMap64(), "impressions": 0, "withDevice": 0}
            for name, scope in batch
        }
        for path in files:
            with path.open("rb") as stream:
                chunk = pickle.load(stream)
            for name, _scope in batch:
                project = chunk["projects"].get(name)
                if project:
                    accumulator = accumulators[name]
                    buckets = [*project["channels"].values(), project["unclassified"]]
                    for bucket in buckets:
                        accumulator["impressions"] += bucket["impressions"]
                        accumulator["withDevice"] += bucket["withDevice"]
                        accumulator["devices"] |= bucket["devices"]
            del chunk
        for name, _scope in batch:
            accumulator = accumulators[name]
            reach = len(accumulator["devices"])
            rows.append({
                "project": name,
                "scope": accumulator["scope"],
                "impressions": accumulator["impressions"],
                "impressionsWithDevice": accumulator["withDevice"],
                "reach": reach,
                "frequency": round(accumulator["impressions"] / reach, 4) if reach else None,
                "byChannel": [],
                "unclassified": None,
            })
        del accumulators
        gc.collect()

    rows.sort(key=lambda row: (
        {"object": 0, "brand": 1, "unassigned": 2}.get(row["scope"], 3),
        -row["impressions"],
        row["project"].casefold(),
    ))

    output_path = Path(args.out)
    existing = json.loads(output_path.read_text(encoding="utf-8"))
    total_reach = int(existing["total"]["reach"])
    result = {
        "period": {"from": "2026-07-01", "to": "2026-07-31"},
        "projectId": project_id,
        "method": "Target Ads Raw Data API v2; project-only streaming Roaring Bitmap unions; unchanged exact period Total reach reused from the previous calculation",
        "important": "Reach is deduplicated independently for Total and each project. Do not sum project Reach rows.",
        "channelClassificationVersion": "none-2026-08-19",
        "projectClassificationVersion": "v2-2026-08-19",
        "placementMetaCount": placement_meta_count,
        "total": {
            "label": "Level Group",
            "impressions": total_impressions,
            "impressionsWithDevice": total_with_device,
            "reach": total_reach,
            "frequency": round(total_impressions / total_reach, 4) if total_reach else None,
            "deviceIdCoverage": round(total_with_device / total_impressions, 6) if total_impressions else None,
        },
        "byChannel": [],
        "byProject": rows,
        "unclassified": None,
        "unknownPlacementImpressions": unknown_placements,
        "jobs": sorted(jobs, key=lambda job: job["from"]),
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"chunks": len(files), "total": result["total"], "projects": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
