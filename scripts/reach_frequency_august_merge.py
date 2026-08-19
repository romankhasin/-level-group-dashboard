#!/usr/bin/env python3
"""Merge leaf-only chunk artifacts into monthly object/channel/global reach."""
import argparse,json,pickle
from pathlib import Path
from pyroaring import BitMap64
import export_reach_frequency_august_fast as fast

CHANNELS=fast.base.CHANNEL_ORDER
def leaf(): return {"impressions":0,"withDevice":0,"devices":BitMap64()}
def project(scope): return {"scope":scope,"channels":{c:leaf() for c in CHANNELS},"unclassified":leaf()}
def merge(dst,src): dst["impressions"]+=src["impressions"]; dst["withDevice"]+=src["withDevice"]; dst["devices"] |= src["devices"]
def serial(name,b):
    r=len(b["devices"]); i=b["impressions"]
    return {"channel":name,"impressions":i,"impressionsWithDevice":b["withDevice"],"reach":r,"frequency":round(i/r,4) if r else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--out",required=True); ap.add_argument("--from-date",required=True); ap.add_argument("--to-date",required=True); a=ap.parse_args()
    files=sorted(Path(a.input).rglob("*.pkl"));
    if not files: raise RuntimeError("No chunk pickle files found")
    projects={}; total_imps=0; total_with=0; unknown=0; jobs=[]; project_id=12787; meta_count=0
    for path in files:
        with open(path,"rb") as f: d=pickle.load(f)
        total_imps+=d["totalImpressions"]; total_with+=d["totalWithDevice"]; unknown+=d["unknownPlacementImpressions"]; project_id=d["projectId"]; meta_count=max(meta_count,d["placementMetaCount"])
        jobs.append({"from":d["from"],"to":d["to"],"jobId":d["jobId"],"rows":d["totalImpressions"],"rowsWithDevice":d["totalWithDevice"]})
        for name,sp in d["projects"].items():
            dp=projects.get(name)
            if dp is None: dp=project(sp["scope"]); projects[name]=dp
            for c in CHANNELS: merge(dp["channels"][c],sp["channels"][c])
            merge(dp["unclassified"],sp["unclassified"])

    global_total=BitMap64(); global_channels={c:BitMap64() for c in CHANNELS}; global_unc=BitMap64(); channel_imps={c:0 for c in CHANNELS}; channel_with={c:0 for c in CHANNELS}; unc_imps=0; unc_with=0; rows=[]
    for name,p in projects.items():
        ptotal=BitMap64(); pimps=0; pwith=0; chrows=[]
        for c in CHANNELS:
            b=p["channels"][c]
            if b["impressions"]:
                ptotal |= b["devices"]; global_total |= b["devices"]; global_channels[c] |= b["devices"]; channel_imps[c]+=b["impressions"]; channel_with[c]+=b["withDevice"]; pimps+=b["impressions"]; pwith+=b["withDevice"]; chrows.append(serial(c,b))
        u=p["unclassified"]
        if u["impressions"]:
            ptotal |= u["devices"]; global_total |= u["devices"]; global_unc |= u["devices"]; unc_imps+=u["impressions"]; unc_with+=u["withDevice"]; pimps+=u["impressions"]; pwith+=u["withDevice"]
        pr=len(ptotal); rows.append({"project":name,"scope":p["scope"],"impressions":pimps,"impressionsWithDevice":pwith,"reach":pr,"frequency":round(pimps/pr,4) if pr else None,"byChannel":[],"unclassified":None})
    rows.sort(key=lambda r:({"object":0,"brand":1,"unassigned":2}.get(r["scope"],3),-r["impressions"],r["project"].casefold()))
    total_reach=len(global_total)
    by_channel=[]
    for c in CHANNELS:
        r=len(global_channels[c]); i=channel_imps[c]; by_channel.append({"channel":c,"impressions":i,"impressionsWithDevice":channel_with[c],"reach":r,"frequency":round(i/r,4) if r else None})
    ur=len(global_unc); unclassified={"channel":"Не классифицировано","impressions":unc_imps,"impressionsWithDevice":unc_with,"reach":ur,"frequency":round(unc_imps/ur,4) if ur else None}
    result={"period":{"from":a.from_date,"to":a.to_date},"projectId":project_id,"method":"Target Ads Raw Data API v2; parallel date chunks; exact 64-bit hashed InteractionDeviceID Roaring Bitmap unions","important":"Reach is deduplicated independently for Total and each project. Do not sum project Reach rows.","channelClassificationVersion":"none-2026-08-19","projectClassificationVersion":"v2-2026-08-19","channelClassificationNotes":None,"projectClassificationNotes":"Known Level developments, Work Нижегородская, regional placements, Остатки, Левел Групп and Премиальные коллекции are reported separately.","placementMetaCount":meta_count,"total":{"label":"Level Group","impressions":total_imps,"impressionsWithDevice":total_with,"reach":total_reach,"frequency":round(total_imps/total_reach,4) if total_reach else None,"deviceIdCoverage":round(total_with/total_imps,6) if total_imps else None},"byChannel":[],"byProject":rows,"unclassified":None,"unknownPlacementImpressions":unknown,"jobs":sorted(jobs,key=lambda x:x["from"])}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"chunks":len(files),"total":result["total"],"projects":len(rows)},ensure_ascii=False))
if __name__=="__main__": main()
