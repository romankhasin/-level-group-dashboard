#!/usr/bin/env python3
"""July 2026 exact Reach/Frequency by Level Group, channel, object and object×channel.

Uses Target Ads Raw Data API v2 and 64-bit Roaring bitmaps to keep exact unions of
hashed InteractionDeviceID values without the memory overhead of Python sets.
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

from pyroaring import BitMap64

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reach_frequency_july_2026.json"
API = "https://api.targetads.io"
DATE_FROM = dt.date(2026, 7, 1)
DATE_TO = dt.date(2026, 7, 31)
CHUNK_DAYS = 3
POLL_SECONDS = 5
JOB_TIMEOUT_SECONDS = 20 * 60

CHANNEL_ORDER = ["Programmatic", "Smart TV", "Маркетплейсы", "Медийка", "Target"]
PROGRAMMATIC_SOURCES = {
    "roxot", "astralab", "buzzoola", "mobidriven", "adspector", "qbid",
    "qbid баннеры", "innovation lab", "adheads", "vox", "digital alliance",
    "solta", "plazkart", "onetarget",
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
    if payload.get("ErrorCode"):
        raise RuntimeError(
            f"Target Ads error: {payload.get('ErrorCode')} {payload.get('ErrorMessage')} "
            f"{payload.get('Errors') or payload.get('ErrorsField')}"
        )
    return payload


def load_placement_meta(token: str, project_id: int) -> dict[str, dict]:
    payload = request_json(
        api_url("/v1/meta/campaigns", project_id, {"active": "false", "include_creative": "false"}),
        token,
    )
    result: dict[str, dict] = {}
    for item in payload.get("meta") or []:
        if not isinstance(item, dict):
            continue
        placement_id = str(item.get("placement_id") or "").strip()
        if not placement_id:
            continue
        result[placement_id] = {
            "placementName": str(item.get("placement_name") or "").strip(),
            "sourceName": str(item.get("source_name") or item.get("media_source_name") or "").strip(),
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
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()


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
    payload = request_json(
        api_url("/v2/reports/raw_reports", project_id),
        token,
        {
            "Fields": ["InteractionDate", "InteractionDeviceID", "InteractionPlacementId"],
            "DateFrom": start.isoformat(),
            "DateTo": end.isoformat(),
            "InteractionType": "Impression",
        },
    )
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"No job_id returned for {start}..{end}: {payload}")
    return job_id


def wait_job(token: str, project_id: int, job_id: str) -> str:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        payload = request_json(api_url(f"/v2/jobs/{urllib.parse.quote(job_id, safe='')}", project_id), token)
        status = str(payload.get("status") or "").upper()
        if status == "DONE":
            url = str(payload.get("download_url") or "").strip()
            if not url:
                raise RuntimeError("DONE job has no download_url")
            return url
        if status in {"FAILED", "CANCELLED", "EXPIRED"}:
            raise RuntimeError(f"Job {job_id} ended as {status}: {payload}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for Target Ads job {job_id}")
        time.sleep(POLL_SECONDS)


def device_hash(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def empty_channel_bucket() -> dict:
    return {"impressions": 0, "devices": BitMap64(), "sources": set()}


def empty_project_bucket(scope: str) -> dict:
    return {
        "scope": scope,
        "impressions": 0,
        "devices": BitMap64(),
        "channels": {name: empty_channel_bucket() for name in CHANNEL_ORDER},
        "unclassified": empty_channel_bucket(),
    }


def process_download(download_url: str, placement_meta: dict[str, dict], total_devices: BitMap64,
                     by_channel: dict[str, dict], by_project: dict[str, dict],
                     unclassified: dict, counters: dict[str, int]) -> dict:
    rows = 0
    rows_with_device = 0
    req = urllib.request.Request(download_url, headers={"User-Agent": "LevelReachFrequency/1.0"})
    with urllib.request.urlopen(req, timeout=300) as response:
        with gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"InteractionDeviceID", "InteractionPlacementId"}
                if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                    raise RuntimeError(f"Unexpected raw CSV fields: {reader.fieldnames}")
                for row in reader:
                    rows += 1
                    counters["impressions"] += 1
                    placement_id = str(row.get("InteractionPlacementId") or "").strip()
                    meta = placement_meta.get(placement_id)
                    if meta:
                        placement_name = meta["placementName"]
                        source_name = meta["sourceName"]
                    else:
                        placement_name = ""
                        source_name = ""
                        counters["unknownPlacementImpressions"] += 1

                    channel = classify_channel(source_name, placement_name)
                    ch_bucket = by_channel[channel] if channel else unclassified
                    ch_bucket["impressions"] += 1
                    if source_name:
                        ch_bucket["sources"].add(source_name)

                    project_name, scope = classify_project(placement_name)
                    project = by_project.setdefault(project_name, empty_project_bucket(scope))
                    project["impressions"] += 1
                    pch_bucket = project["channels"][channel] if channel else project["unclassified"]
                    pch_bucket["impressions"] += 1
                    if source_name:
                        pch_bucket["sources"].add(source_name)

                    device_id = str(row.get("InteractionDeviceID") or "").strip()
                    if not device_id:
                        continue
                    h = device_hash(device_id)
                    rows_with_device += 1
                    counters["impressionsWithDevice"] += 1
                    total_devices.add(h)
                    ch_bucket["devices"].add(h)
                    project["devices"].add(h)
                    pch_bucket["devices"].add(h)
    return {"rows": rows, "rowsWithDevice": rows_with_device}


def serialize_channel(name: str, bucket: dict) -> dict:
    reach = len(bucket["devices"])
    impressions = int(bucket["impressions"])
    return {
        "channel": name,
        "impressions": impressions,
        "impressionsWithDevice": impressions,
        "reach": reach,
        "frequency": round(impressions / reach, 4) if reach else None,
        "sources": sorted(bucket["sources"], key=str.casefold),
    }


def serialize_project(name: str, bucket: dict) -> dict:
    reach = len(bucket["devices"])
    impressions = int(bucket["impressions"])
    rows = [serialize_channel(ch, bucket["channels"][ch]) for ch in CHANNEL_ORDER]
    rows = [row for row in rows if row["impressions"] > 0]
    unc = serialize_channel("Не классифицировано", bucket["unclassified"])
    return {
        "project": name,
        "scope": bucket["scope"],
        "impressions": impressions,
        "impressionsWithDevice": impressions,
        "reach": reach,
        "frequency": round(impressions / reach, 4) if reach else None,
        "byChannel": rows,
        "unclassified": unc if unc["impressions"] else None,
    }


def project_sort_key(row: dict) -> tuple:
    rank = {"object": 0, "brand": 1, "unassigned": 2}.get(row.get("scope"), 3)
    return rank, -int(row.get("impressions") or 0), str(row.get("project") or "").casefold()


def main() -> None:
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    meta = load_placement_meta(token, project_id)
    total_devices = BitMap64()
    by_channel = {name: empty_channel_bucket() for name in CHANNEL_ORDER}
    by_project: dict[str, dict] = {}
    unclassified = empty_channel_bucket()
    counters = {"impressions": 0, "impressionsWithDevice": 0, "unknownPlacementImpressions": 0}

    pending = []
    for idx, (start, end) in enumerate(date_chunks(DATE_FROM, DATE_TO), 1):
        job_id = create_raw_job(token, project_id, start, end)
        pending.append({"chunk": idx, "from": start, "to": end, "jobId": job_id})
        print(json.dumps({"submitted": idx, "from": start.isoformat(), "to": end.isoformat()}, ensure_ascii=False), flush=True)

    jobs = []
    for item in pending:
        url = wait_job(token, project_id, item["jobId"])
        stats = process_download(url, meta, total_devices, by_channel, by_project, unclassified, counters)
        jobs.append({"from": item["from"].isoformat(), "to": item["to"].isoformat(), "jobId": item["jobId"], **stats})
        print(json.dumps({"completed": item["chunk"], **stats, "uniqueDevicesSoFar": len(total_devices), "projects": len(by_project)}, ensure_ascii=False), flush=True)

    total_reach = len(total_devices)
    channel_rows = [serialize_channel(name, by_channel[name]) for name in CHANNEL_ORDER]
    project_rows = [serialize_project(name, bucket) for name, bucket in by_project.items()]
    project_rows.sort(key=project_sort_key)
    unc = serialize_channel("Не классифицировано", unclassified)

    result = {
        "period": {"from": DATE_FROM.isoformat(), "to": DATE_TO.isoformat()},
        "projectId": project_id,
        "method": "Target Ads Raw Data API v2; exact 64-bit hashed InteractionDeviceID unions using Roaring Bitmap",
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
        "projectClassificationNotes": "Known Level development names are normalized; regional placements are rolled into the same object. Brand and unknown prefixes are kept separately.",
        "placementMetaCount": len(meta),
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
        "unclassified": unc,
        "unknownPlacementImpressions": counters["unknownPlacementImpressions"],
        "jobs": jobs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": result["total"], "projects": len(project_rows)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
