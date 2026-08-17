#!/usr/bin/env python3
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "targetads_meta.json"
API = "https://api.targetads.io"

def main():
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    params = urllib.parse.urlencode({"project_id": project_id, "active": "false", "include_creative": "false"})
    req = urllib.request.Request(
        f"{API}/v1/meta/campaigns?{params}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        payload = json.load(response)
    if payload.get("ErrorCode"):
        raise RuntimeError(str(payload))
    rows = []
    for item in payload.get("meta") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "placement_id": item.get("placement_id"),
            "placement_name": item.get("placement_name"),
            "source_id": item.get("source_id") or item.get("media_source_id"),
            "source_name": item.get("source_name") or item.get("media_source_name"),
            "marketing_name": item.get("marketing_name"),
            "campaign_id": item.get("campaign_id"),
            "campaign_name": item.get("campaign_name"),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"projectId": project_id, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(rows)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
