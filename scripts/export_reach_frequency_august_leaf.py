#!/usr/bin/env python3
"""Exact July 2026 reach/frequency using leaf-level Roaring Bitmap unions.

Each impression is assigned to exactly one object×channel leaf. A device hash is
stored only in that leaf. Object totals, channel totals and Level Group Total are
then derived by bitmap union, avoiding duplicate in-memory copies of the same ID.
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

# Target Ads naming is the source of truth for media channel.  These are the
# only confirmed channel tokens in the current pipeline; do not infer a
# channel from source_name or add a token before it exists in that naming.
TOKEN_CHANNELS = {"prg": "Programmatic", "med": "Медийка", "mrk": "Маркетплейсы"}
CHANNEL_ORDER = list(dict.fromkeys(TOKEN_CHANNELS.values()))
TOKEN_RE = re.compile(r"(?<![a-z0-9а-я])(" + "|".join(TOKEN_CHANNELS) + r")(?![a-z0-9а-я])", re.IGNORECASE)

PROJECT_ALIASES = [
    ("павелецкая сити","Павелецкая Сити"),
    ("нижегородская w","Work Нижегородская"),("нижегородская","Level Нижегородская"),
    ("южнопортовая регионы","Level Южнопортовая"),("южнопортовая","Level Южнопортовая"),
    ("мичуринский регионы","Level Мичуринский"),("мичуринский","Level Мичуринский"),
    ("лесной регионы","Level Лесной"),("лесной","Level Лесной"),
    ("звенигородская","Level Звенигородская"),("селигерская","Level Селигерская"),
    ("войковская","Level Войковская"),("воронцовская","Level Воронцовская"),
    ("мечникова","Level Мечникова"),("свободы","Level Свободы"),
    ("саввинская 27","Level Саввинская"),("саввинская 17","Level Саввинская"),("саввинская","Level Саввинская"),
    ("бауманская","Level Бауманская"),("причальный","Level Причальный"),
]


def api_url(path: str, project_id: int, extra: dict[str,str] | None = None) -> str:
    params = {"project_id": str(project_id)}
    if extra: params.update(extra)
    return f"{API}{path}?{urllib.parse.urlencode(params)}"


def request_json(url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Authorization":f"Bearer {token}","Accept":"application/json",**({"Content-Type":"application/json"} if data is not None else {})}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=300) as response: payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Target Ads HTTP {exc.code}: {exc.read().decode(errors='replace')[:3000]}") from exc
    if not isinstance(payload, dict): raise RuntimeError("Unexpected Target Ads response")
    if payload.get("ErrorCode"): raise RuntimeError(f"Target Ads error: {payload.get('ErrorCode')} {payload.get('ErrorMessage')} {payload.get('Errors') or payload.get('ErrorsField')}")
    return payload


def load_meta(token: str, project_id: int) -> dict[str,dict]:
    payload = request_json(api_url("/v1/meta/campaigns", project_id, {"active":"false","include_creative":"false"}), token)
    result = {}
    for item in payload.get("meta") or []:
        if not isinstance(item, dict): continue
        pid = str(item.get("placement_id") or "").strip()
        if pid:
            result[pid] = {
                "placementName": str(item.get("placement_name") or "").strip(),
                "marketingName": str(item.get("marketing_name") or "").strip(),
                "campaignName": str(item.get("campaign_name") or "").strip(),
            }
    return result


def classify_channel(placement_name: str, marketing_name: str = "", campaign_name: str = "") -> tuple[str | None, str | None, str | None]:
    """Classify strictly by a confirmed token in Target Ads naming fields."""
    for field, value in (("placement_name", placement_name), ("marketing_name", marketing_name), ("campaign_name", campaign_name)):
        match = TOKEN_RE.search(value.casefold())
        if match:
            token = match.group(1).casefold()
            return TOKEN_CHANNELS[token], token, field
    return None, None, None


def classify_project(placement_name: str) -> tuple[str,str]:
    placement = re.sub(r"\s+"," ",placement_name.casefold().replace("ё","е")).strip()
    if not placement: return "Без объекта","unassigned"
    if "регионы" in placement: return "Регионы","object"
    if "остатки" in placement: return "Остатки","brand"
    if "премиум" in placement or "premium" in placement: return "Премиальные коллекции","brand"
    if "зонтик" in placement or "level group" in placement: return "Левел Групп","brand"
    for alias,label in PROJECT_ALIASES:
        if alias in placement: return label,"object"
    prefix = re.split(r"\s*//\s*|\s+/\s+", placement_name, maxsplit=1)[0].strip()
    return (f"Прочее · {prefix}","unassigned") if prefix and len(prefix)<=80 else ("Без объекта","unassigned")


def chunks(start: dt.date, end: dt.date):
    cur = start
    while cur <= end:
        stop = min(cur + dt.timedelta(days=CHUNK_DAYS-1), end); yield cur,stop; cur = stop + dt.timedelta(days=1)


def create_job(token: str, project_id: int, start: dt.date, end: dt.date) -> str:
    payload = request_json(api_url("/v2/reports/raw_reports",project_id),token,{"Fields":["InteractionDate","InteractionDeviceID","InteractionPlacementId"],"DateFrom":start.isoformat(),"DateTo":end.isoformat(),"InteractionType":"Impression"})
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id: raise RuntimeError(f"No job_id for {start}..{end}")
    return job_id


def wait_job(token: str, project_id: int, job_id: str) -> str:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        payload = request_json(api_url(f"/v2/jobs/{urllib.parse.quote(job_id,safe='')}",project_id),token)
        status = str(payload.get("status") or "").upper()
        if status == "DONE":
            url = str(payload.get("download_url") or "").strip()
            if not url: raise RuntimeError("DONE job has no download_url")
            return url
        if status in {"FAILED","CANCELLED","EXPIRED"}: raise RuntimeError(f"Job {job_id}: {status}")
        if time.monotonic() >= deadline: raise RuntimeError(f"Timeout {job_id}")
        time.sleep(POLL_SECONDS)


def h64(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode(),digest_size=8).digest(),"big")


def leaf() -> dict:
    return {"impressions":0,"withDevice":0,"devices":BitMap64(),"sources":set()}


def project_bucket(scope: str) -> dict:
    return {"scope":scope,"channels":{name:leaf() for name in CHANNEL_ORDER},"unclassified":leaf()}


def process(url: str, meta: dict[str,dict], projects: dict[str,dict], counters: dict[str,int]) -> dict:
    rows=0; with_device=0
    req=urllib.request.Request(url,headers={"User-Agent":"LevelReachFrequency/2.0"})
    with urllib.request.urlopen(req,timeout=300) as response:
      with gzip.GzipFile(fileobj=response) as compressed:
       with io.TextIOWrapper(compressed,encoding="utf-8-sig",newline="") as stream:
        reader=csv.DictReader(stream)
        if not reader.fieldnames or not {"InteractionDeviceID","InteractionPlacementId"}.issubset(set(reader.fieldnames)): raise RuntimeError(f"Unexpected fields: {reader.fieldnames}")
        for row in reader:
            rows+=1; counters["impressions"]+=1
            pid=str(row.get("InteractionPlacementId") or "").strip(); m=meta.get(pid)
            if m: placement=m["placementName"]; channel,_,_=classify_channel(placement,m.get("marketingName", ""),m.get("campaignName", ""))
            else: placement=""; channel=None; counters["unknownPlacementImpressions"]+=1
            pname,scope=classify_project(placement)
            project=projects.setdefault(pname,project_bucket(scope)); bucket=project["channels"][channel] if channel else project["unclassified"]
            bucket["impressions"]+=1
            device=str(row.get("InteractionDeviceID") or "").strip()
            if device:
                with_device+=1; counters["impressionsWithDevice"]+=1; bucket["withDevice"]+=1; bucket["devices"].add(h64(device))
    return {"rows":rows,"rowsWithDevice":with_device}


def union_bitmap(bitmaps) -> BitMap64:
    result=BitMap64()
    for bitmap in bitmaps: result |= bitmap
    return result


def serial_channel(name: str, bucket: dict) -> dict:
    reach=len(bucket["devices"]); impressions=int(bucket["impressions"])
    return {"channel":name,"impressions":impressions,"impressionsWithDevice":int(bucket["withDevice"]),"reach":reach,"frequency":round(impressions/reach,4) if reach else None,"sources":sorted(bucket["sources"],key=str.casefold)}


def main() -> None:
    token=os.environ.get("TARGETADS_TOKEN","").strip()
    if not token: raise RuntimeError("TARGETADS_TOKEN is empty")
    project_id=int(os.environ.get("TARGETADS_PROJECT_ID","").strip() or "12787")
    meta=load_meta(token,project_id); projects={}; counters={"impressions":0,"impressionsWithDevice":0,"unknownPlacementImpressions":0}
    pending=[]
    for i,(start,end) in enumerate(chunks(DATE_FROM,DATE_TO),1):
        job=create_job(token,project_id,start,end); pending.append({"chunk":i,"from":start,"to":end,"jobId":job}); print(json.dumps({"submitted":i,"from":start.isoformat(),"to":end.isoformat()},ensure_ascii=False),flush=True)
    jobs=[]
    for item in pending:
        stats=process(wait_job(token,project_id,item["jobId"]),meta,projects,counters); jobs.append({"from":item["from"].isoformat(),"to":item["to"].isoformat(),"jobId":item["jobId"],**stats}); print(json.dumps({"completed":item["chunk"],**stats,"projects":len(projects)},ensure_ascii=False),flush=True)

    channel_acc={name:leaf() for name in CHANNEL_ORDER}; unc_acc=leaf(); project_rows=[]; all_leaf_bitmaps=[]
    for pname,p in projects.items():
        project_bitmaps=[]; project_impressions=0; project_with_device=0; channel_rows=[]
        for ch in CHANNEL_ORDER:
            b=p["channels"][ch]
            if b["impressions"]:
                project_bitmaps.append(b["devices"]); all_leaf_bitmaps.append(b["devices"]); project_impressions+=b["impressions"]; project_with_device+=b["withDevice"]
                acc=channel_acc[ch]; acc["impressions"]+=b["impressions"]; acc["withDevice"]+=b["withDevice"]; acc["devices"] |= b["devices"]; acc["sources"].update(b["sources"])
                channel_rows.append(serial_channel(ch,b))
        u=p["unclassified"]
        if u["impressions"]:
            project_bitmaps.append(u["devices"]); all_leaf_bitmaps.append(u["devices"]); project_impressions+=u["impressions"]; project_with_device+=u["withDevice"]
            unc_acc["impressions"]+=u["impressions"]; unc_acc["withDevice"]+=u["withDevice"]; unc_acc["devices"] |= u["devices"]; unc_acc["sources"].update(u["sources"])
        pdevices=union_bitmap(project_bitmaps); reach=len(pdevices)
        project_rows.append({"project":pname,"scope":p["scope"],"impressions":project_impressions,"impressionsWithDevice":project_with_device,"reach":reach,"frequency":round(project_impressions/reach,4) if reach else None,"byChannel":channel_rows,"unclassified":serial_channel("Не классифицировано",u) if u["impressions"] else None})
    project_rows.sort(key=lambda r:({"object":0,"brand":1,"unassigned":2}.get(r["scope"],3),-r["impressions"],r["project"].casefold()))
    total_devices=union_bitmap(all_leaf_bitmaps); total_reach=len(total_devices)
    result={
      "period":{"from":DATE_FROM.isoformat(),"to":DATE_TO.isoformat()},"projectId":project_id,
      "method":"Target Ads Raw Data API v2; exact 64-bit hashed InteractionDeviceID unions using leaf-level Roaring Bitmap",
      "important":"Reach is deduplicated independently for Total, each channel, each object and each object×channel pair. Do not sum child Reach rows.",
      "channelClassificationVersion":"v1-2026-08-17","projectClassificationVersion":"v1-2026-08-17",
      "channelClassificationNotes":{"Programmatic":"Roxot, Astralab, Buzzoola, Mobidriven, Adspector, q.bid, Innovation Lab, Adheads, VOX, Digital Alliance, SOLTA, Plazkart, OneTarget","Smart TV":"MTS, Streamingads, Rutube","Маркетплейсы":"Ozon, Avito, Wildberries, Пятерочка and Yandex Market placements","Target":"VK Ads / ВКР placements","Медийка":"Other identified media sources, including YandexMI and non-Market UrbanAds placements"},
      "projectClassificationNotes":"Known Level development names are normalized; regional placements roll into the same object. Brand and unknown prefixes remain separate.",
      "placementMetaCount":len(meta),
      "total":{"label":"Level Group","impressions":counters["impressions"],"impressionsWithDevice":counters["impressionsWithDevice"],"reach":total_reach,"frequency":round(counters["impressions"]/total_reach,4) if total_reach else None,"deviceIdCoverage":round(counters["impressionsWithDevice"]/counters["impressions"],6) if counters["impressions"] else None},
      "byChannel":[serial_channel(ch,channel_acc[ch]) for ch in CHANNEL_ORDER],"byProject":project_rows,"unclassified":serial_channel("Не классифицировано",unc_acc),"unknownPlacementImpressions":counters["unknownPlacementImpressions"],"jobs":jobs}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"total":result["total"],"projects":len(project_rows)},ensure_ascii=False),flush=True)

if __name__=="__main__": main()
