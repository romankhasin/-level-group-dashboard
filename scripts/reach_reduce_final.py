#!/usr/bin/env python3
"""Build final July reach JSON from monthly channel reducer artifacts."""
import argparse,gc,json,pickle
from pathlib import Path
from pyroaring import BitMap64

ORDER=["Programmatic","Smart TV","Маркетплейсы","Медийка","Target"]

def ptotal(scope): return {"scope":scope,"impressions":0,"withDevice":0,"devices":BitMap64(),"byChannel":[],"unclassified":None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    files=sorted(Path(a.input).rglob("*.pkl"));
    if not files: raise RuntimeError("No channel artifacts")
    projects={}; global_total=BitMap64(); by_channel=[]; unclassified=None; total_imps=0; total_with=0
    for path in files:
        with open(path,"rb") as f: d=pickle.load(f)
        channel=d["channel"]; ctotal=BitMap64(); cimps=0; cwith=0
        for name,src in d["projects"].items():
            reach=len(src["devices"]); imps=src["impressions"]
            dst=projects.get(name)
            if dst is None: dst=ptotal(src["scope"]); projects[name]=dst
            dst["impressions"]+=imps; dst["withDevice"]+=src["withDevice"]; dst["devices"] |= src["devices"]
            ctotal |= src["devices"]; cimps+=imps; cwith+=src["withDevice"]
            metric={"channel":"Не классифицировано" if channel=="__UNC__" else channel,"impressions":imps,"impressionsWithDevice":src["withDevice"],"reach":reach,"frequency":round(imps/reach,4) if reach else None}
            if channel=="__UNC__": dst["unclassified"]=metric
            else: dst["byChannel"].append(metric)
        cr=len(ctotal); metric={"channel":"Не классифицировано" if channel=="__UNC__" else channel,"impressions":cimps,"impressionsWithDevice":cwith,"reach":cr,"frequency":round(cimps/cr,4) if cr else None}
        if channel=="__UNC__": unclassified=metric
        else: by_channel.append(metric)
        global_total |= ctotal; total_imps+=cimps; total_with+=cwith
        del d,ctotal; gc.collect()
    by_channel.sort(key=lambda r:ORDER.index(r["channel"]) if r["channel"] in ORDER else 99)
    rows=[]
    for name,p in projects.items():
        r=len(p["devices"]); p["byChannel"].sort(key=lambda x:ORDER.index(x["channel"]) if x["channel"] in ORDER else 99)
        rows.append({"project":name,"scope":p["scope"],"impressions":p["impressions"],"impressionsWithDevice":p["withDevice"],"reach":r,"frequency":round(p["impressions"]/r,4) if r else None,"byChannel":p["byChannel"],"unclassified":p["unclassified"]})
    rows.sort(key=lambda r:({"object":0,"brand":1,"unassigned":2}.get(r["scope"],3),-r["impressions"],r["project"].casefold()))
    tr=len(global_total)
    result={"period":{"from":"2026-07-01","to":"2026-07-31"},"projectId":12787,"method":"Target Ads Raw Data API v2; parallel chunk/channel MapReduce; exact monthly unions of 64-bit hashed InteractionDeviceID using Roaring Bitmap","important":"Reach is deduplicated independently for Total, each channel, each object and each object×channel pair. Do not sum child Reach rows.","channelClassificationVersion":"v1-2026-08-17","projectClassificationVersion":"v1-2026-08-17","total":{"label":"Level Group","impressions":total_imps,"impressionsWithDevice":total_with,"reach":tr,"frequency":round(total_imps/tr,4) if tr else None,"deviceIdCoverage":round(total_with/total_imps,6) if total_imps else None},"byChannel":by_channel,"byProject":rows,"unclassified":unclassified,"unknownPlacementImpressions":unclassified["impressions"] if unclassified else 0}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"total":result["total"],"projects":len(rows)},ensure_ascii=False))
if __name__=="__main__": main()
