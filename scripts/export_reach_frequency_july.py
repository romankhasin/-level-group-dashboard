#!/usr/bin/env python3
"""One-off July 2026 Reach/Frequency export from Target Ads Raw Data API v2.

The aggregated API currently returns no rows for project 12787, so this prototype
counts unique InteractionDeviceID values directly from Impression raw data.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reach_frequency_july_2026.json"
API = "https://api.targetads.io"
DATE_FROM = "2026-07-01"
DATE_TO = "2026-07-31"
POLL_SECONDS = 5
TIMEOUT_SECONDS = 25 * 60


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
            return json.load(response)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Target Ads HTTP {exc.code}: {details[:3000]}") from exc


def ensure_ok(payload: dict, action: str) -> dict:
    if payload.get("ErrorCode"):
        raise RuntimeError(
            f"Target Ads error while {action}: {payload.get('ErrorCode')} "
            f"{payload.get('ErrorMessage')} {payload.get('Errors')}"
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
        result[placement_id] = {
            "placementId": placement_id,
            "placementName": str(item.get("placement_name") or "").strip(),
            "sourceId": str(item.get("source_id") or "").strip(),
            "sourceName": str(item.get("source_name") or "").strip() or "(unknown source)",
            "marketingName": str(item.get("marketing_name") or "").strip(),
        }
    return result


def create_raw_job(token: str, project_id: int) -> str:
    payload = ensure_ok(
        request_json(
            api_url("/v2/reports/raw_reports", project_id),
            token,
            {
                "Fields": [
                    "InteractionDate",
                    "InteractionDeviceID",
                    "InteractionPlacementId",
                    "InteractionNewCookie",
                ],
                "DateFrom": DATE_FROM,
                "DateTo": DATE_TO,
                "InteractionType": "Impression",
            },
        ),
        "creating July Impression raw job",
    )
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"No job_id returned: {payload}")
    return job_id


def wait_job(token: str, project_id: int, job_id: str) -> str:
    deadline = time.monotonic() + TIMEOUT_SECONDS
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


def parse_bool_new_cookie(value: object) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def build_metrics(download_url: str, placement_meta: dict[str, dict]) -> dict:
    total_impressions = 0
    total_with_device = 0
    total_devices: set[int] = set()
    total_stable_devices: set[int] = set()
    by_source: dict[str, dict] = {}
    unknown_placement_impressions = 0

    req = urllib.request.Request(download_url, headers={"User-Agent": "LevelGroupDashboard/1.0"})
    with urllib.request.urlopen(req, timeout=300) as response:
        with gzip.GzipFile(fileobj=response) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"InteractionDeviceID", "InteractionPlacementId"}
                if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                    raise RuntimeError(f"Unexpected raw CSV fields: {reader.fieldnames}")

                for row in reader:
                    total_impressions += 1
                    placement_id = str(row.get("InteractionPlacementId") or "").strip()
                    meta = placement_meta.get(placement_id)
                    if meta:
                        source_name = meta["sourceName"]
                    else:
                        source_name = "(unknown source)"
                        unknown_placement_impressions += 1

                    bucket = by_source.setdefault(
                        source_name,
                        {"impressions": 0, "withDevice": 0, "devices": set(), "stableDevices": set()},
                    )
                    bucket["impressions"] += 1

                    device_id = str(row.get("InteractionDeviceID") or "").strip()
                    if not device_id:
                        continue
                    h = device_hash(device_id)
                    total_with_device += 1
                    total_devices.add(h)
                    bucket["withDevice"] += 1
                    bucket["devices"].add(h)

                    is_new = parse_bool_new_cookie(row.get("InteractionNewCookie"))
                    if is_new is False:
                        total_stable_devices.add(h)
                        bucket["stableDevices"].add(h)

    def row_metrics(name: str, item: dict) -> dict:
        reach = len(item["devices"])
        impressions = int(item["impressions"])
        return {
            "source": name,
            "impressions": impressions,
            "impressionsWithDevice": int(item["withDevice"]),
            "reach": reach,
            "frequency": round(impressions / reach, 4) if reach else None,
            "stableReachDiagnostic": len(item["stableDevices"]),
        }

    source_rows = [row_metrics(name, item) for name, item in by_source.items()]
    source_rows.sort(key=lambda row: row["impressions"], reverse=True)
    total_reach = len(total_devices)
    return {
        "total": {
            "impressions": total_impressions,
            "impressionsWithDevice": total_with_device,
            "reach": total_reach,
            "frequency": round(total_impressions / total_reach, 4) if total_reach else None,
            "stableReachDiagnostic": len(total_stable_devices),
        },
        "bySource": source_rows,
        "unknownPlacementImpressions": unknown_placement_impressions,
    }


def main() -> None:
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")

    placement_meta = load_placement_meta(token, project_id)
    job_id = create_raw_job(token, project_id)
    print(json.dumps({"jobId": job_id, "placements": len(placement_meta)}, ensure_ascii=False))
    download_url = wait_job(token, project_id, job_id)
    metrics = build_metrics(download_url, placement_meta)

    result = {
        "period": {"from": DATE_FROM, "to": DATE_TO},
        "projectId": project_id,
        "method": "Raw Data API v2 Impression events; unique InteractionDeviceID hashed to 64-bit values in memory",
        "important": "This prototype computes reach directly from raw device IDs because Aggregated API returned zero rows for this project/date. Frequency = all impressions / unique device IDs. The stableReachDiagnostic field is diagnostic only and is not used in frequency.",
        "placementMetaCount": len(placement_meta),
        **metrics,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": result["total"], "sourceCount": len(result["bySource"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
