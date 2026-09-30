#!/usr/bin/env python3
"""Exact July 2026 Target Ads reach by media source across six selected projects."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import pickle
import re
import urllib.request
from collections import Counter
from pathlib import Path

from pyroaring import BitMap64

import export_reach_frequency_july_leaf as targetads


SOURCES = (
    "МТС", "Пятерочка", "Adheads", "Adspector", "Avito", "buzzoola",
    "Ozon", "qbid", "qbid баннеры", "Roxot", "Rutube", "streamingads",
    "UrbanAds",
)
PROJECTS = (
    "Лесной", "Звенигородская", "Селигерская", "Нижегородская",
    "Мичуринский", "Южнопортовая",
)


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()


def source_of(value: str) -> str | None:
    name = normalized(value).replace("q.bid", "qbid")
    aliases = {normalized(source): source for source in SOURCES}
    aliases["adsheads"] = "Adheads"
    return aliases.get(name)


def project_of(value: str) -> str | None:
    name = normalized(value)
    if "нижегородская" in name and (
        "work" in name or re.search(r"нижегородская\s+w\b", name)
    ):
        return None
    return next((project for project in PROJECTS if normalized(project) in name), None)


def placement_map(meta: dict[str, dict]) -> dict[str, tuple[str, str]]:
    result = {}
    for placement_id, item in meta.items():
        source = source_of(str(item.get("sourceName") or ""))
        project = project_of(str(item.get("placementName") or ""))
        if source and project:
            result[str(placement_id)] = (source, project)
    return result


def run_chunk(args: argparse.Namespace) -> None:
    token = os.environ["TARGETADS_TOKEN"].strip()
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    meta = placement_map(targetads.load_meta(token, project_id))
    buckets = {source: {"impressions": 0, "withDevice": 0, "devices": BitMap64()} for source in SOURCES}
    by_project = Counter()
    all_rows = unknown_placements = matched_rows = 0
    start = dt.date.fromisoformat(args.from_date)
    end = dt.date.fromisoformat(args.to_date)
    job_id = targetads.create_job(token, project_id, start, end)
    url = targetads.wait_job(token, project_id, job_id)
    request = urllib.request.Request(url, headers={"User-Agent": "LevelReachFrequency/selected-sources"})
    with urllib.request.urlopen(request, timeout=300) as response:
        with gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"InteractionDeviceID", "InteractionPlacementId"}
                if not reader.fieldnames or not required.issubset(reader.fieldnames):
                    raise RuntimeError(f"Unexpected fields: {reader.fieldnames}")
                for row in reader:
                    all_rows += 1
                    placement_id = str(row.get("InteractionPlacementId") or "").strip()
                    match = meta.get(placement_id)
                    if not match:
                        if not placement_id:
                            unknown_placements += 1
                        continue
                    source, project = match
                    matched_rows += 1
                    by_project[project] += 1
                    bucket = buckets[source]
                    bucket["impressions"] += 1
                    device_id = str(row.get("InteractionDeviceID") or "").strip()
                    if device_id:
                        bucket["withDevice"] += 1
                        bucket["devices"].add(targetads.h64(device_id))
    data = {
        "from": args.from_date, "to": args.to_date, "projectId": project_id,
        "jobId": job_id, "allRows": all_rows, "matchedRows": matched_rows,
        "emptyPlacementRows": unknown_placements, "matchedPlacementCount": len(meta),
        "byProjectImpressions": dict(by_project), "sources": buckets,
    }
    with Path(args.out).open("wb") as handle:
        pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)
    print(json.dumps({key: data[key] for key in ("from", "to", "allRows", "matchedRows", "matchedPlacementCount")}, ensure_ascii=False), flush=True)


def run_merge(args: argparse.Namespace) -> None:
    files = sorted(Path(args.input).glob("july-*.pkl"))
    if len(files) != 8:
        raise RuntimeError(f"Expected 8 July chunks, found {len(files)}")
    expected_from = dt.date(2026, 7, 1)
    rows = {source: {"impressions": 0, "withDevice": 0, "devices": BitMap64()} for source in SOURCES}
    by_project = Counter()
    chunks = []
    project_ids = set()
    for file in files:
        with file.open("rb") as handle:
            chunk = pickle.load(handle)
        start = dt.date.fromisoformat(chunk["from"])
        end = dt.date.fromisoformat(chunk["to"])
        if start != expected_from or end < start:
            raise RuntimeError(f"Gap or overlap at {file.name}: {start}..{end}")
        expected_from = end + dt.timedelta(days=1)
        project_ids.add(chunk["projectId"])
        chunks.append({key: chunk[key] for key in ("from", "to", "allRows", "matchedRows", "emptyPlacementRows", "matchedPlacementCount")})
        by_project.update(chunk["byProjectImpressions"])
        for source in SOURCES:
            target = rows[source]
            incoming = chunk["sources"][source]
            target["impressions"] += incoming["impressions"]
            target["withDevice"] += incoming["withDevice"]
            target["devices"] |= incoming["devices"]
    if expected_from != dt.date(2026, 8, 1) or len(project_ids) != 1:
        raise RuntimeError("July period or Target Ads project ID is inconsistent")
    if set(by_project) != set(PROJECTS):
        raise RuntimeError(f"Project coverage differs: {dict(by_project)}")
    result_rows = []
    for source in SOURCES:
        item = rows[source]
        reach = len(item["devices"])
        impressions = item["impressions"]
        result_rows.append({
            "source": source, "impressions": impressions,
            "impressionsWithDevice": item["withDevice"], "reach": reach,
            "frequency": round(impressions / reach, 4) if reach else None,
        })
    result = {
        "period": {"from": "2026-07-01", "to": "2026-07-31"},
        "projectId": next(iter(project_ids)), "projects": list(PROJECTS),
        "method": "Target Ads Raw Data API v2 Impression rows; exact per-source union of hashed InteractionDeviceID across six selected projects",
        "notes": "Includes regional variants of the six projects. Excludes Work Нижегородская. A source with zero impressions has no measured frequency.",
        "bySource": result_rows, "byProjectImpressions": dict(by_project),
        "chunks": chunks,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"period": result["period"], "bySource": result_rows}, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    chunk = subparsers.add_parser("chunk")
    chunk.add_argument("--from-date", required=True)
    chunk.add_argument("--to-date", required=True)
    chunk.add_argument("--out", required=True)
    merge = subparsers.add_parser("merge")
    merge.add_argument("--input", required=True)
    merge.add_argument("--out", required=True)
    args = parser.parse_args()
    (run_chunk if args.command == "chunk" else run_merge)(args)


if __name__ == "__main__":
    main()
