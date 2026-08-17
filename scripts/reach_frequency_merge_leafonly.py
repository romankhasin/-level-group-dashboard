#!/usr/bin/env python3
"""Merge leaf-only chunk artifacts into monthly object/channel/global reach."""
import argparse,json,pickle
from pathlib import Path
from pyroaring import BitMap64
import export_reach_frequency_july_fast as fast

CHANNELS=fast.base.CHANNEL_ORDER
def leaf(): return {"impressions":0,"withDevice":0,"devices":BitMap64()}
def project(scope): return {"scope":scope,"channels":{c:leaf() for c in CHANNELS},"unclassified":leaf()}
def merge(dst,src): dst["impressions"]+=src["impressions"]; dst["withDevice"]+=src["withDevice"]; dst["devices"] |= src["devices"]
def serial(name,b):
    r=len(b["devices"]); i=b["impressions"]
    return {"channel":name,"impressions":i,"impressionsWithDevice":b["withDevice"],"reach":r,"frequency":round(i/r,4) if r else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    files=sorted(Path(a.input).rglob("*.pkl"));
    if not files: raise RuntimeError("No chunk pickle files found")
    projects={}; total_imps=0; total_with=0; unknown=0; recognized=0; no_token=0; token_impressions={token:0 for token in fast.base.TOKEN_CHANNELS}; jobs=[]; project_id=12787; meta_count=0
    for path in files:
        with open(path,"rb") as f: d=pickle.load(f)
        total_imps+=d["totalImpressions"]; total_with+=d["totalWithDevice"]; unknown+=d["unknownPlacementImpressions"]; recognized+=d.get("recognizedTokenImpressions",0); no_token+=d.get("noRecognizedChannelTokenImpressions",0); project_id=d["projectId"]; meta_count=max(meta_count,d["placementMetaCount"])
        for token, count in d.get("tokenImpressions",{}).items(): token_impressions[token]=token_impressions.get(token,0)+count
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
        pr=len(ptotal); rows.append({"project":name,"scope":p["scope"],"impressions":pimps,"impressionsWithDevice":pwith,"reach":pr,"frequency":round(pimps/pr,4) if pr else None,"byChannel":chrows,"unclassified":serial("Не классифицировано",u) if u["impressions"] else None})
    rows.sort(key=lambda r:({"object":0,"brand":1,"unassigned":2}.get(r["scope"],3),-r["impressions"],r["project"].casefold()))
    total_reach=len(global_total)
    by_channel=[]
    for c in CHANNELS:
        r=len(global_channels[c]); i=channel_imps[c]; by_channel.append({"channel":c,"impressions":i,"impressionsWithDevice":channel_with[c],"reach":r,"frequency":round(i/r,4) if r else None})
    ur=len(global_unc); unclassified={"channel":"Не классифицировано","impressions":unc_imps,"impressionsWithDevice":unc_with,"reach":ur,"frequency":round(unc_imps/ur,4) if ur else None}
    result={"period":{"from":"2026-07-01","to":"2026-07-31"},"projectId":project_id,"method":"Target Ads Raw Data API v2; 11 parallel chunks; exact 64-bit hashed InteractionDeviceID monthly Roaring Bitmap unions","important":"Reach is deduplicated independently for Total, each channel, each object and each object×channel pair. Do not sum child Reach rows.","channelClassificationVersion":"v2-2026-08-17-token-naming","projectClassificationVersion":"v1-2026-08-17","channelClassificationNotes":{"Programmatic":"Target Ads naming token prg","Медийка":"Target Ads naming token med","Маркетплейсы":"Target Ads naming token mrk"},"projectClassificationNotes":"Known Level development names are normalized; regional placements roll into the same object. Brand and unknown prefixes remain separate.","placementMetaCount":meta_count,"total":{"label":"Level Group","impressions":total_imps,"impressionsWithDevice":total_with,"reach":total_reach,"frequency":round(total_imps/total_reach,4) if total_reach else None,"deviceIdCoverage":round(total_with/total_imps,6) if total_imps else None},"byChannel":by_channel,"byProject":rows,"unclassified":unclassified,"unknownPlacementImpressions":unknown,"channelTokenDiagnostics":{"classifier":"Target Ads placement_name → marketing_name → campaign_name; source_name is not used","mapping":fast.base.TOKEN_CHANNELS,"recognizedTokenImpressions":recognized,"noRecognizedChannelTokenImpressions":no_token,"tokenImpressions":token_impressions,"tokenCoverage":round(recognized/total_imps,6) if total_imps else 0.0},"jobs":sorted(jobs,key=lambda x:x["from"])}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"chunks":len(files),"total":result["total"],"projects":len(rows)},ensure_ascii=False))
if __name__=="__main__": main()
