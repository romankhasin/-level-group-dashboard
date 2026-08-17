#!/usr/bin/env python3
"""Merge parallel July reach chunks and write the compact report JSON."""
import argparse,gzip,json,pickle
from pathlib import Path
from pyroaring import BitMap64
import export_reach_frequency_july_fast as fast

CHANNELS=fast.base.CHANNEL_ORDER

def bucket(): return {"impressions":0,"withDevice":0,"devices":BitMap64()}
def project_bucket(scope): return {"scope":scope,"total":bucket(),"channels":{c:bucket() for c in CHANNELS},"unclassified":bucket()}
def merge_bucket(dst,src):
    dst["impressions"]+=src["impressions"]; dst["withDevice"]+=src["withDevice"]; dst["devices"] |= src["devices"]
def serialize(name,b):
    reach=len(b["devices"]); imps=int(b["impressions"])
    return {"channel":name,"impressions":imps,"impressionsWithDevice":int(b["withDevice"]),"reach":reach,"frequency":round(imps/reach,4) if reach else None}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",required=True); p.add_argument("--out",required=True); a=p.parse_args()
    total=bucket(); channels={c:bucket() for c in CHANNELS}; unc=bucket(); projects={}; unknown=0; jobs=[]; project_id=12787; meta_count=0
    files=sorted(Path(a.input).rglob("*.pkl.gz"))
    if not files: raise RuntimeError("No chunk artifacts found")
    for path in files:
        with gzip.open(path,"rb") as f: data=pickle.load(f)
        project_id=data.get("projectId",project_id); meta_count=max(meta_count,data.get("placementMetaCount",0)); unknown+=data.get("unknownPlacementImpressions",0)
        merge_bucket(total,data["total"])
        for c in CHANNELS: merge_bucket(channels[c],data["channels"][c])
        merge_bucket(unc,data["unclassified"])
        for name,srcp in data["projects"].items():
            dst=projects.get(name)
            if dst is None: dst=project_bucket(srcp["scope"]); projects[name]=dst
            merge_bucket(dst["total"],srcp["total"])
            for c in CHANNELS: merge_bucket(dst["channels"][c],srcp["channels"][c])
            merge_bucket(dst["unclassified"],srcp["unclassified"])
        jobs.append({"from":data["from"],"to":data["to"],"jobId":data["jobId"],"rows":data["total"]["impressions"],"rowsWithDevice":data["total"]["withDevice"]})
    project_rows=[]
    for name,pb in projects.items():
        t=pb["total"]; reach=len(t["devices"]); imps=t["impressions"]
        channel_rows=[serialize(c,pb["channels"][c]) for c in CHANNELS if pb["channels"][c]["impressions"]]
        u=serialize("Не классифицировано",pb["unclassified"])
        project_rows.append({"project":name,"scope":pb["scope"],"impressions":imps,"impressionsWithDevice":t["withDevice"],"reach":reach,"frequency":round(imps/reach,4) if reach else None,"byChannel":channel_rows,"unclassified":u if u["impressions"] else None})
    project_rows.sort(key=lambda r:({"object":0,"brand":1,"unassigned":2}.get(r["scope"],3),-r["impressions"],r["project"].casefold()))
    total_reach=len(total["devices"]); total_imps=total["impressions"]
    result={"period":{"from":"2026-07-01","to":"2026-07-31"},"projectId":project_id,"method":"Target Ads Raw Data API v2; 11 parallel date chunks; exact monthly unions of 64-bit hashed InteractionDeviceID using Roaring Bitmap","important":"Reach is deduplicated independently for Total, each channel, each object and each object×channel pair. Do not sum child Reach rows.","channelClassificationVersion":"v1-2026-08-17","projectClassificationVersion":"v1-2026-08-17","channelClassificationNotes":{"Programmatic":"Roxot, Astralab, Buzzoola, Mobidriven, Adspector, q.bid, Innovation Lab, Adheads, VOX, Digital Alliance, SOLTA, Plazkart, OneTarget","Smart TV":"MTS, Streamingads, Rutube","Маркетплейсы":"Ozon, Avito, Wildberries, Пятерочка and Yandex Market placements","Target":"VK Ads / ВКР placements","Медийка":"Other identified media sources, including YandexMI and non-Market UrbanAds placements"},"projectClassificationNotes":"Known Level development names are normalized; regional placements roll into the same object. Brand and unknown prefixes remain separate.","placementMetaCount":meta_count,"total":{"label":"Level Group","impressions":total_imps,"impressionsWithDevice":total["withDevice"],"reach":total_reach,"frequency":round(total_imps/total_reach,4) if total_reach else None,"deviceIdCoverage":round(total["withDevice"]/total_imps,6) if total_imps else None},"byChannel":[serialize(c,channels[c]) for c in CHANNELS],"byProject":project_rows,"unclassified":serialize("Не классифицировано",unc),"unknownPlacementImpressions":unknown,"jobs":sorted(jobs,key=lambda x:x["from"])}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"chunks":len(files),"total":result["total"],"projects":len(project_rows)},ensure_ascii=False))
if __name__=="__main__": main()
