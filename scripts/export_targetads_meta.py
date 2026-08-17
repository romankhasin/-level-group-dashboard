#!/usr/bin/env python3
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "targetads_meta.json"
API = "https://api.targetads.io"
TOKEN_CHANNELS = {"prg": "Programmatic", "med": "Медийка", "mrk": "Маркетплейсы"}
TOKEN_RE = re.compile(r"(?<![a-z0-9а-я])(prg|med|mrk)(?![a-z0-9а-я])", re.IGNORECASE)

def channel_token(item):
    for field in ("placement_name", "marketing_name", "campaign_name"):
        value = str(item.get(field) or "")
        match = TOKEN_RE.search(value.casefold())
        if match:
            token = match.group(1).casefold()
            return token, TOKEN_CHANNELS[token], field
    return None, None, None

def main():
    token = os.environ.get("TARGETADS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id = int(os.environ.get("TARGETADS_PROJECT_ID", "").strip() or "12787")
    params = urllib.parse.urlencode({"project_id": project_id, "active": "false", "include_creative": "true"})
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
        token, channel, token_field = channel_token(item)
        rows.append({
            "placement_id": item.get("placement_id"),
            "placement_name": item.get("placement_name"),
            "source_id": item.get("source_id") or item.get("media_source_id"),
            "source_name": item.get("source_name") or item.get("media_source_name"),
            "marketing_name": item.get("marketing_name"),
            "campaign_id": item.get("campaign_id"),
            "campaign_name": item.get("campaign_name"),
            "channel_token": token,
            "channel": channel,
            "channel_token_field": token_field,
            # Preserve the API's native creative payload for naming diagnostics.
            # No creative field is used as a channel fallback until its token
            # structure has been inspected and confirmed.
            "creative": item.get("creative"),
            "creative_name": item.get("creative_name"),
            "creative_id": item.get("creative_id"),
            "metadata_fields": sorted(item.keys()),
            "creative_related_fields": {key: value for key, value in item.items() if "creative" in key.casefold()},
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"projectId": project_id, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(rows)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
