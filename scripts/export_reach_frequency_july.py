#!/usr/bin/env python3
"""July 2026 Reach/Frequency export from Target Ads Raw Data API v2.

Target Ads Raw Data v2 accepts at most three days per async job. The exporter
creates all non-overlapping three-day July jobs first, then streams every gzip
CSV and keeps unions of hashed device IDs for the whole month, by source, by
Level Group media channel, by object, and by object × channel.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reach_frequency_july_2026.json"
API = "https://api.targetads.io"
DATE_FROM = dt.date(2026, 7, 1)
DATE_TO = dt.date(2026, 7, 31)
POLL_SECONDS = 5
JOB_TIMEOUT_SECONDS = 20 * 60
CHUNK_DAYS = 3

CHANNEL_ORDER = ["Programmatic", "Smart TV", "Маркетплейсы", "Медийка", "Target"]

PROGRAMMATIC_SOURCES = {
    "roxot",
    "astralab",
    "buzzoola",
    "mobidriven",
    "adspector",
    "qbid",
    "qbid баннеры",
    "innovation lab",
    "adheads",
    "vox",
    "digital alliance",
    "solta",
    "plazkart",
    "onetarget",
}
SMART_TV_SOURCES = {"мтс", "streamingads", "rutube"}
MARKETPLACE_SOURCES = {"ozon", "avito", "wildberries", "пятерочка"}
TARGET_SOURCES = {"вкр", "vk", "vk ads", "vkads"}

PROJECT_ALIASES = [
    ("павелецкая сити", "Павелецкая Сити"),
    ("нижегородская w", "Level Нижегородская"),
    ("нижегородская", "Level Нижегородская"),
    ("южнопортовая регионы", "Level Южнопортовая"),
    ("южнопортовая", "Level Южнопортовая"),
    ("мичуринский регионы", "Level Мичуринский"),
    ("мичуринский", "Level Мичуринский"),
    ("лесной регионы", "Level Лесной"),
    ("лесной", "Level Лесной"),
    ("звенигородская", "Level Звенигородская"),
    ("селигерская", "Level Селигерская"),
    ("войковская", "Level Войковская"),
    ("воронцовская", "Level Воронцовская"),
    ("мечникова", "Level Мечникова"),
    ("свободы", "Level Свободы"),
    ("саввинская 27", "Level Саввинская"),
    ("саввинская 17", "Level Саввинская"),
    ("саввинская", "Level Саввинская"),
    ("бауманская", "Level Бауманская"),
    ("причальный", "Level Причальный"),
    ("level group", "Level Group"),
]
BRAND_ALIASES = [
    ("зонтик", "Level Group"),
    ("премиум", "Level Premium"),
    ("остатки", "Остатки"),
]


def api_url(path: str, project_id: int, extra: dict[str, str] | None = None) -> str:
    params = {"project_id": str(project_id)}
    if extra:
        params.update(extra)
    return f"{API}{path}?{urllib.parse.urlencode(params)}"


def request_json(url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Target Ads HTTP {exc.code}: {details[:3000]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected non-object Target Ads response")
    return payload


def ensure_ok(payload: dict, action: str) -> dict:
    if payload.get("ErrorCode"):
        raise RuntimeError(
            f"Target Ads error while {action}: {payload.get('ErrorCode')} "
            f"{payload.get('ErrorMessage')} {payload.get('Errors') or payload.get('ErrorsField')}"
        )
    return payload


def load_placement_meta(token: str, project_id: int) -> dict[str, dict]:
    payload = ensure_ok(
        request_json(
            api_url(
                "/v1/meta/campaigns",
                project_id,
                {"active": "false", "include_creative": "false"},
            ),
            token,
        ),
        "loading campaign metadata",
    )
    result: dict[str, dict] = {}
    for item in payload.get("meta") or []:
        if not isinstance(item, dict):
            continue
        placement_id = str(item.get("placement_id") or "").strip()
        if not placement_id:
            continue
        placement_name = str(item.get("placement_name") or "").strip()
        source_name = str(item.get("source_name") or item.get("media_source_name") or "").strip()
        source_id = str(item.get("source_id") or item.get("media_source_id") or "").strip()
        marketing_name = str(item.get("marketing_name") or "").strip()
        group_name = source_name or placement_name or f"Placement {placement_id}"
        result[placement_id] = {
            "placementId": placement_id,
            "placementName": placement_name,
            "sourceId": source_id,
            "sourceName": source_name,
            "marketingName": marketing_name,
            "groupName": group_name,
        }
    return result


def classify_channel(source_name: str, placement_name: str) -> str | None:
    source = source_name.casefold().strip()
    placement = placement_name.casefold().strip()

    if "market yandex" in placement or "яндекс маркет" in placement:
        return "Маркетплейсы"
    if "vkads" in placement or "vk ads" in placement or "вк ads" in placement:
        return "Target"

    if source in TARGET_SOURCES or source.startswith("вкр"):
        return "Target"
    if source in MARKETPLACE_SOURCES:
        return "Маркетплейсы"
    if source in SMART_TV_SOURCES:
        return "Smart TV"
    if source in PROGRAMMATIC_SOURCES:
        return "Programmatic"
    if source:
        return "Медийка"
    return None


def normalize_project_text(value: str) -> str:
    text = value.casefold().replace("ё", "е")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def classify_project(placement_name: str) -> tuple[str, str]:
    placement = normalize_project_text(placement_name)
    if not placement:
        return "Без объекта", "unassigned"

    for alias, label in PROJECT_ALIASES:
        if alias in placement:
            return label, "object" if label != "Level Group" else "brand"
    for alias, label in BRAND_ALIASES:
        if alias in placement:
            return label, "brand"

    prefix = re.split(r"\s*//\s*|\s+/\s+", placement_name, maxsplit=1)[0].strip()
    if prefix and len(prefix) <= 80:
        return f"Прочее · {prefix}", "unassigned"
    return "Без объекта", "unassigned"


def date_chunks(start: dt.date, end: dt.date):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + dt.timedelta(days=CHUNK_DAYS - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + dt.timedelta(days=1)


def create_raw_job(token: str, project_id: int, start: dt.date, end: dt.date) -> str:
    payload = ensure_ok(
        request_json(
            api_url("/v2/reports/raw_reports", project_id),
            token,
            {
                "Fields": [
                    "InteractionDate",
                    "InteractionDeviceID",
                    "InteractionPlacementId",
                ],
                "DateFrom": start.isoformat(),
                "DateTo": end.isoformat(),
                "InteractionType": "Impression",
            },
        ),
        f"creating Impression raw job {start}..{end}",
    )
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"No job_id returned for {start}..{end}: {payload}")
    return job_id


def wait_job(token: str, project_id: int, job_id: str) -> str:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        payload = ensure_ok(
            request_json(
                api_url(f"/v2/jobs/{urllib.parse.quote(job_id, safe='')}", project_id),
                token,
            ),
            f"checking job {job_id}",
        )
        status = str(payload.get("status") or "").upper()
        if status == "DONE":
            download_url = str(payload.get("download_url") or "").strip()
            if not download_url:
                raise RuntimeError("Job is DONE but download_url is empty")
            return download_url
        if status in {"FAILED", "CANCELLED", "EXPIRED"}:
            raise RuntimeError(f"Job {job_id} ended as {status}: {payload}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for Target Ads job {job_id}")
        time.sleep(POLL_SECONDS)


def device_hash(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def empty_channel_bucket() -> dict:
    return {"impressions": 0, "impressionsWithDevice": 0, "devices": set(), "sources": set()}


def empty_project_bucket(scope: str) -> dict:
    return {
        "scope": scope,
        "impressions": 0,
        "impressionsWithDevice": 0,
        "devices": set(),
        "channels": {name: empty_channel_bucket() for name in CHANNEL_ORDER},
        "unclassified": empty_channel_bucket(),
    }


def process_download(
    download_url: str,
    placement_meta: dict[str, dict],
    total_devices: set[int],
    by_group: dict[str, dict],
    by_channel: dict[str, dict],
    by_project: dict[str, dict],
    unclassified: dict,
    counters: dict[str, int],
) -> dict:
    chunk_rows = 0
    chunk_with_device = 0
    req = urllib.request.Request(download_url, headers={"User-Agent": "LevelGroupDashboard/1.0"})
    with urllib.request.urlopen(req, timeout=300) as response:
        with gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"InteractionDeviceID", "InteractionPlacementId"}
                if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                    raise RuntimeError(f"Unexpected raw CSV fields: {reader.fieldnames}")

                for row in reader:
                    chunk_rows += 1
                    counters["impressions"] += 1
                    placement_id = str(row.get("InteractionPlacementId") or "").strip()
                    meta = placement_meta.get(placement_id)
                    if meta:
                        group_name = meta["groupName"]
                        placement_name = meta["placementName"]
                        source_name = meta["sourceName"]
                    else:
                        group_name = f"Unknown placement {placement_id or '(empty)'}"
                        placement_name = ""
                        source_name = ""
                        counters["unknownPlacementImpressions"] += 1

                    source_bucket = by_group.setdefault(
                        group_name,
                        {
                            "source": group_name,
                            "sourceName": source_name,
                            "placementExample": placement_name,
                            "impressions": 0,
                            "impressionsWithDevice": 0,
                            "devices": set(),
                        },
                    )
                    source_bucket["impressions"] += 1

                    channel = classify_channel(source_name, placement_name)
                    channel_bucket = by_channel[channel] if channel else unclassified
                    channel_bucket["impressions"] += 1
                    if source_name:
                        channel_bucket["sources"].add(source_name)

                    project_name, project_scope = classify_project(placement_name)
                    project_bucket = by_project.setdefault(project_name, empty_project_bucket(project_scope))
                    project_bucket["impressions"] += 1
                    project_channel_bucket = (
                        project_bucket["channels"][channel] if channel else project_bucket["unclassified"]
                    )
                    project_channel_bucket["impressions"] += 1
                    if source_name:
                        project_channel_bucket["sources"].add(source_name)

                    device_id = str(row.get("InteractionDeviceID") or "").strip()
                    if not device_id:
                        continue
                    h = device_hash(device_id)
                    chunk_with_device += 1
                    counters["impressionsWithDevice"] += 1
                    total_devices.add(h)

                    source_bucket["impressionsWithDevice"] += 1
                    source_bucket["devices"].add(h)
                    channel_bucket["impressionsWithDevice"] += 1
                    channel_bucket["devices"].add(h)
                    project_bucket["impressionsWithDevice"] += 1
                    project_bucket["devices"].add(h)
                    project_channel_bucket["impressionsWithDevice"] += 1
                    project_channel_bucket["devices"].add(h)

    return {"rows": chunk_rows, "rowsWithDevice": chunk_with_device}


def serialize_bucket(name: str, bucket: dict) -> dict:
    reach = len(bucket["devices"])
    impressions = int(bucket["impressions"])
    return {
        "channel": name,
        "impressions": impressions,
        "impressionsWithDevice": int(bucket["impressionsWithDevice"]),
        "reach": reach,
        "frequency": round(impressions / reach, 4) if reach else None,
        "sources": sorted(bucket["sources"], key=str.casefold),
    }


def serialize_project(name: str, bucket: dict) -> dict:
    reach = len(bucket["devices"])
    impressions = int(bucket["impressions"])
    channel_rows = [serialize_bucket(channel, bucket["channels"][channel]) for channel in CHANNEL_ORDER]
    channel_rows = [row for row in channel_rows if row["impressions"] > 0]
    project_unclassified = serialize_bucket("Не классифицировано", bucket["unclassified"])
    return {
        "project": name,
        "scope": bucket["scope"],
        "impressions": impressions,
        "impressionsWithDevice": int(bucket["impressionsWithDevice"]),
        "reach": reach,
        "frequency": round(impressions / reach, 4) if reach else None,
        "byChannel": channel_rows,
        "unclassified": project_unclassified if project_unclassified["impressions"] else None,
    }


def project_sort_key(row: dict) -> tuple:
    scope_rank = {"object": 0, "brand": 1, "unassigned": 2}.get(row.get("scope"), 3)
    return (scope_rank, -int(row.get("impressions") or 0), str(row.get("project") or "").casefold())


def main() -> None:
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")

    placement_meta = load_placement_meta(token, project_id)
    total_devices: set[int] = set()
    by_group: dict[str, dict] = {}
    by_channel = {name: empty_channel_bucket() for name in CHANNEL_ORDER}
    by_project: dict[str, dict] = {}
    unclassified = empty_channel_bucket()
    counters = {
        "impressions": 0,
        "impressionsWithDevice": 0,
        "unknownPlacementImpressions": 0,
    }

    pending_jobs: list[dict] = []
    for index, (start, end) in enumerate(date_chunks(DATE_FROM, DATE_TO), start=1):
        job_id = create_raw_job(token, project_id, start, end)
        pending_jobs.append({"chunk": index, "from": start, "to": end, "jobId": job_id})
        print(
            json.dumps(
                {"submitted": index, "from": start.isoformat(), "to": end.isoformat(), "jobId": job_id},
                ensure_ascii=False,
            ),
            flush=True,
        )

    jobs: list[dict] = []
    for pending in pending_jobs:
        download_url = wait_job(token, project_id, pending["jobId"])
        chunk_stats = process_download(
            download_url,
            placement_meta,
            total_devices,
            by_group,
            by_channel,
            by_project,
            unclassified,
            counters,
        )
        jobs.append(
            {
                "from": pending["from"].isoformat(),
                "to": pending["to"].isoformat(),
                "jobId": pending["jobId"],
                **chunk_stats,
            }
        )
        print(
            json.dumps(
                {"completed": pending["chunk"], **chunk_stats, "uniqueDevicesSoFar": len(total_devices)},
                ensure_ascii=False,
            ),
            flush=True,
        )

    source_rows: list[dict] = []
    for item in by_group.values():
        reach = len(item["devices"])
        impressions = int(item["impressions"])
        source_rows.append(
            {
                "source": item["source"],
                "sourceName": item["sourceName"],
                "placementExample": item["placementExample"],
                "impressions": impressions,
                "impressionsWithDevice": int(item["impressionsWithDevice"]),
                "reach": reach,
                "frequency": round(impressions / reach, 4) if reach else None,
            }
        )
    source_rows.sort(key=lambda row: row["impressions"], reverse=True)

    channel_rows = [serialize_bucket(name, by_channel[name]) for name in CHANNEL_ORDER]
    unclassified_row = serialize_bucket("Не классифицировано", unclassified)
    project_rows = [serialize_project(name, bucket) for name, bucket in by_project.items()]
    project_rows.sort(key=project_sort_key)

    total_reach = len(total_devices)
    result = {
        "period": {"from": DATE_FROM.isoformat(), "to": DATE_TO.isoformat()},
        "projectId": project_id,
        "method": "Target Ads Raw Data API v2, Impression events; monthly union of InteractionDeviceID across 3-day jobs",
        "important": "Reach is deduplicated independently for Total, each media channel, each object, each object-channel pair and each source. Do not sum Reach rows to obtain parent totals. Frequency = impressions / deduplicated device reach.",
        "channelClassificationVersion": "v1-2026-08-17",
        "projectClassificationVersion": "v1-2026-08-17",
        "channelClassificationNotes": {
            "Programmatic": "Roxot, Astralab, Buzzoola, Mobidriven, Adspector, q.bid, Innovation Lab, Adheads, VOX, Digital Alliance, SOLTA, Plazkart, OneTarget",
            "Smart TV": "MTS, Streamingads, Rutube",
            "Маркетплейсы": "Ozon, Avito, Wildberries, Пятерочка and Yandex Market placements",
            "Target": "VK Ads / ВКР placements",
            "Медийка": "Other identified media sources, including YandexMI and non-Market UrbanAds placements",
        },
        "projectClassificationNotes": "Known Level development names are normalized; regional placements are rolled into the same object. Brand and unknown placement prefixes are kept separately and never silently discarded.",
        "placementMetaCount": len(placement_meta),
        "total": {
            "label": "Level Group",
            "impressions": counters["impressions"],
            "impressionsWithDevice": counters["impressionsWithDevice"],
            "reach": total_reach,
            "frequency": round(counters["impressions"] / total_reach, 4) if total_reach else None,
            "deviceIdCoverage": round(counters["impressionsWithDevice"] / counters["impressions"], 6) if counters["impressions"] else None,
        },
        "byChannel": channel_rows,
        "byProject": project_rows,
        "unclassified": unclassified_row,
        "bySource": source_rows,
        "unknownPlacementImpressions": counters["unknownPlacementImpressions"],
        "jobs": jobs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "total": result["total"],
                "byChannel": channel_rows,
                "projectCount": len(project_rows),
                "unclassified": unclassified_row,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
