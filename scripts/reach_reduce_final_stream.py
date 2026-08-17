#!/usr/bin/env python3
"""Memory-safe final reducer for July Reach/Frequency."""
from __future__ import annotations
import argparse,gc,json,pickle,re
from pathlib import Path
from tempfile import TemporaryDirectory
from pyroaring import BitMap64
ORDER=["Programmatic","Медийка","Маркетплейсы"]
def safe_name(v): return re.sub(r"[^0-9A-Za-zА-Яа-я_-]+","_",v).strip("_")[:120] or "project"
def metric(label,impressions,with_device,devices):
    reach=len(devices)
    return {"channel":label,"impressions":int(impressions),"impressionsWithDevice":int(with_device),"reach":reach,"frequency":round(impressions/reach,4) if reach else None}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    files=sorted(Path(a.input).rglob("*.pkl"), key=lambda p: (0 if "unclassified" in p.name else 1, p.name))
    if not files: raise RuntimeError("No monthly channel artifacts found")
    global_total=None; by_channel=[]; unclassified=None; projects={}; total_imps=0; total_with=0
    with TemporaryDirectory(prefix="reach-projects-") as td:
        tmp=Path(td)
        for path in files:
            with path.open("rb") as f: data=pickle.load(f)
            channel=data["channel"]; label="Не классифицировано" if channel=="__UNC__" else channel
            ctotal=BitMap64(); cimps=0; cwith=0
            for pname,src in data["projects"].items():
                p=projects.setdefault(pname,{"scope":src["scope"],"impressions":0,"withDevice":0,"channels":[],"unclassified":None,"bitmapFiles":[]})
                devices=src["devices"]; imps=int(src["impressions"]); w=int(src["withDevice"])
                ctotal|=devices; cimps+=imps; cwith+=w; p["impressions"]+=imps; p["withDevice"]+=w
                row=metric(label,imps,w,devices)
                if channel=="__UNC__": p["unclassified"]=row
                else: p["channels"].append(row)
                d=tmp/safe_name(pname); d.mkdir(parents=True,exist_ok=True); bp=d/f"{safe_name(label)}.pkl"
                with bp.open("wb") as f: pickle.dump(devices,f,protocol=pickle.HIGHEST_PROTOCOL)
                p["bitmapFiles"].append(str(bp))
            crow=metric(label,cimps,cwith,ctotal)
            if channel=="__UNC__": unclassified=crow
            else: by_channel.append(crow)
            # Start from the largest bucket (unclassified) without copying its
            # bitmap.  This avoids the previous peak of two full-month bitmaps.
            if global_total is None:
                global_total=ctotal
            else:
                global_total |= ctotal
                del ctotal
            total_imps+=cimps; total_with+=cwith
            del data; gc.collect()
        rows=[]
        for pname,p in projects.items():
            pt=BitMap64()
            for bp in p["bitmapFiles"]:
                with open(bp,"rb") as f: pt|=pickle.load(f)
            p["channels"].sort(key=lambda r:ORDER.index(r["channel"]) if r["channel"] in ORDER else 99)
            r=len(pt); i=int(p["impressions"])
            rows.append({"project":pname,"scope":p["scope"],"impressions":i,"impressionsWithDevice":int(p["withDevice"]),"reach":r,"frequency":round(i/r,4) if r else None,"byChannel":p["channels"],"unclassified":p["unclassified"]})
            del pt; gc.collect()
    by_channel.sort(key=lambda r:ORDER.index(r["channel"]) if r["channel"] in ORDER else 99)
    rows.sort(key=lambda r:({"object":0,"brand":1,"unassigned":2}.get(r["scope"],3),-r["impressions"],r["project"].casefold()))
    tr=len(global_total) if global_total is not None else 0
    token_impressions={"prg":0,"med":0,"mrk":0}
    channel_to_token={"Programmatic":"prg","Медийка":"med","Маркетплейсы":"mrk"}
    for row in by_channel: token_impressions[channel_to_token[row["channel"]]]=row["impressions"]
    recognized=sum(token_impressions.values())
    no_token=int(unclassified["impressions"]) if unclassified else 0
    result={"period":{"from":"2026-07-01","to":"2026-07-31"},"projectId":12787,"method":"Target Ads Raw Data API v2; memory-safe parallel chunk/channel MapReduce; exact monthly unions of 64-bit hashed InteractionDeviceID using Roaring Bitmap","important":"Reach is deduplicated independently for Total, each channel, each object and each object×channel pair. Do not sum child Reach rows.","channelClassificationVersion":"v2-2026-08-17-token-naming","projectClassificationVersion":"v1-2026-08-17","channelClassificationNotes":{"Programmatic":"Target Ads naming token prg","Медийка":"Target Ads naming token med","Маркетплейсы":"Target Ads naming token mrk"},"total":{"label":"Level Group","impressions":total_imps,"impressionsWithDevice":total_with,"reach":tr,"frequency":round(total_imps/tr,4) if tr else None,"deviceIdCoverage":round(total_with/total_imps,6) if total_imps else None},"byChannel":by_channel,"byProject":rows,"unclassified":unclassified,"unknownPlacementImpressions":int(unclassified["impressions"]) if unclassified else 0,"channelTokenDiagnostics":{"classifier":"Target Ads placement_name → marketing_name → campaign_name; source_name is not used","mapping":{"prg":"Programmatic","med":"Медийка","mrk":"Маркетплейсы"},"recognizedTokenImpressions":recognized,"noRecognizedChannelTokenImpressions":no_token,"tokenImpressions":token_impressions,"tokenCoverage":round(recognized/total_imps,6) if total_imps else 0.0}}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"total":result["total"],"projects":len(rows)},ensure_ascii=False),flush=True)
if __name__=="__main__": main()
