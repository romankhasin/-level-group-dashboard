#!/usr/bin/env python3
"""Fast exact July Reach/Frequency: preclassify placements, store only leaf bitmaps."""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import urllib.request

from pyroaring import BitMap64
import export_reach_frequency_july_leaf as base


def preclassify_meta(raw_meta: dict[str, dict]) -> dict[str, dict]:
    result = {}
    for placement_id, item in raw_meta.items():
        placement = item.get("placementName", "")
        source = item.get("sourceName", "")
        channel = base.classify_channel(source, placement)
        project, scope = base.classify_project(placement)
        result[placement_id] = {
            "source": source,
            "channel": channel,
            "project": project,
            "scope": scope,
        }
    return result


def process_fast(url: str, meta: dict[str, dict], projects: dict[str, dict], counters: dict[str, int]) -> dict:
    rows = 0
    with_device = 0
    req = urllib.request.Request(url, headers={"User-Agent": "LevelReachFrequency/fast"})
    with urllib.request.urlopen(req, timeout=300) as response:
        with gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"InteractionDeviceID", "InteractionPlacementId"}
                if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                    raise RuntimeError(f"Unexpected fields: {reader.fieldnames}")
                for row in reader:
                    rows += 1
                    counters["impressions"] += 1
                    placement_id = str(row.get("InteractionPlacementId") or "").strip()
                    item = meta.get(placement_id)
                    if item:
                        source = item["source"]
                        channel = item["channel"]
                        project_name = item["project"]
                        scope = item["scope"]
                    else:
                        source = ""
                        channel = None
                        project_name = "Без объекта"
                        scope = "unassigned"
                        counters["unknownPlacementImpressions"] += 1

                    project = projects.setdefault(project_name, base.project_bucket(scope))
                    bucket = project["channels"][channel] if channel else project["unclassified"]
                    bucket["impressions"] += 1
                    if source:
                        bucket["sources"].add(source)

                    device_id = str(row.get("InteractionDeviceID") or "").strip()
                    if device_id:
                        with_device += 1
                        counters["impressionsWithDevice"] += 1
                        bucket["withDevice"] += 1
                        bucket["devices"].add(base.h64(device_id))
    return {"rows": rows, "rowsWithDevice": with_device}


def main() -> None:
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")

    meta = preclassify_meta(base.load_meta(token, project_id))
    projects: dict[str, dict] = {}
    counters = {"impressions": 0, "impressionsWithDevice": 0, "unknownPlacementImpressions": 0}

    pending = []
    for index, (start, end) in enumerate(base.chunks(base.DATE_FROM, base.DATE_TO), 1):
        job_id = base.create_job(token, project_id, start, end)
        pending.append({"chunk": index, "from": start, "to": end, "jobId": job_id})
        print(json.dumps({"submitted": index, "from": start.isoformat(), "to": end.isoformat()}, ensure_ascii=False), flush=True)

    jobs = []
    for item in pending:
        download_url = base.wait_job(token, project_id, item["jobId"])
        stats = process_fast(download_url, meta, projects, counters)
        jobs.append({"from": item["from"].isoformat(), "to": item["to"].isoformat(), "jobId": item["jobId"], **stats})
        print(json.dumps({"completed": item["chunk"], **stats, "projects": len(projects)}, ensure_ascii=False), flush=True)

    channel_acc = {name: base.leaf() for name in base.CHANNEL_ORDER}
    unclassified_acc = base.leaf()
    project_rows = []
    all_leaf_bitmaps = []

    for project_name, project in projects.items():
        project_bitmaps = []
        project_impressions = 0
        project_with_device = 0
        channel_rows = []
        for channel in base.CHANNEL_ORDER:
            bucket = project["channels"][channel]
            if not bucket["impressions"]:
                continue
            project_bitmaps.append(bucket["devices"])
            all_leaf_bitmaps.append(bucket["devices"])
            project_impressions += bucket["impressions"]
            project_with_device += bucket["withDevice"]
            acc = channel_acc[channel]
            acc["impressions"] += bucket["impressions"]
            acc["withDevice"] += bucket["withDevice"]
            acc["devices"] |= bucket["devices"]
            acc["sources"].update(bucket["sources"])
            channel_rows.append(base.serial_channel(channel, bucket))

        unc = project["unclassified"]
        if unc["impressions"]:
            project_bitmaps.append(unc["devices"])
            all_leaf_bitmaps.append(unc["devices"])
            project_impressions += unc["impressions"]
            project_with_device += unc["withDevice"]
            unclassified_acc["impressions"] += unc["impressions"]
            unclassified_acc["withDevice"] += unc["withDevice"]
            unclassified_acc["devices"] |= unc["devices"]
            unclassified_acc["sources"].update(unc["sources"])

        project_devices = base.union_bitmap(project_bitmaps)
        reach = len(project_devices)
        project_rows.append({
            "project": project_name,
            "scope": project["scope"],
            "impressions": project_impressions,
            "impressionsWithDevice": project_with_device,
            "reach": reach,
            "frequency": round(project_impressions / reach, 4) if reach else None,
            "byChannel": channel_rows,
            "unclassified": base.serial_channel("Не классифицировано", unc) if unc["impressions"] else None,
        })

    project_rows.sort(key=lambda row: (
        {"object": 0, "brand": 1, "unassigned": 2}.get(row["scope"], 3),
        -row["impressions"],
        row["project"].casefold(),
    ))

    total_devices = base.union_bitmap(all_leaf_bitmaps)
    total_reach = len(total_devices)
    result = {
        "period": {"from": base.DATE_FROM.isoformat(), "to": base.DATE_TO.isoformat()},
        "projectId": project_id,
        "method": "Target Ads Raw Data API v2; preclassified placements + exact 64-bit InteractionDeviceID leaf Roaring Bitmap unions",
        "important": "Reach is deduplicated independently for Total, each channel, each object and each object×channel pair. Do not sum child Reach rows.",
        "channelClassificationVersion": "v1-2026-08-17",
        "projectClassificationVersion": "v1-2026-08-17",
        "channelClassificationNotes": {
            "Programmatic": "Roxot, Astralab, Buzzoola, Mobidriven, Adspector, q.bid, Innovation Lab, Adheads, VOX, Digital Alliance, SOLTA, Plazkart, OneTarget",
            "Smart TV": "MTS, Streamingads, Rutube",
            "Маркетплейсы": "Ozon, Avito, Wildberries, Пятерочка and Yandex Market placements",
            "Target": "VK Ads / ВКР placements",
            "Медийка": "Other identified media sources, including YandexMI and non-Market UrbanAds placements",
        },
        "projectClassificationNotes": "Known Level development names are normalized; regional placements roll into the same object. Brand and unknown prefixes remain separate.",
        "placementMetaCount": len(meta),
        "total": {
            "label": "Level Group",
            "impressions": counters["impressions"],
            "impressionsWithDevice": counters["impressionsWithDevice"],
            "reach": total_reach,
            "frequency": round(counters["impressions"] / total_reach, 4) if total_reach else None,
            "deviceIdCoverage": round(counters["impressionsWithDevice"] / counters["impressions"], 6) if counters["impressions"] else None,
        },
        "byChannel": [base.serial_channel(channel, channel_acc[channel]) for channel in base.CHANNEL_ORDER],
        "byProject": project_rows,
        "unclassified": base.serial_channel("Не классифицировано", unclassified_acc),
        "unknownPlacementImpressions": counters["unknownPlacementImpressions"],
        "jobs": jobs,
    }
    base.OUT.parent.mkdir(parents=True, exist_ok=True)
    base.OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": result["total"], "projects": len(project_rows)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
