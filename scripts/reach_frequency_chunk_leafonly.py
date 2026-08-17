#!/usr/bin/env python3
"""Process one date chunk; persist only object×channel device bitmaps."""
import argparse,csv,gzip,io,os,pickle,urllib.request,datetime as dt
from pyroaring import BitMap64
import export_reach_frequency_july_fast as fast

CHANNELS=fast.base.CHANNEL_ORDER
def leaf(): return {"impressions":0,"withDevice":0,"devices":BitMap64()}
def project(scope): return {"scope":scope,"channels":{c:leaf() for c in CHANNELS},"unclassified":leaf()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--from-date",required=True); ap.add_argument("--to-date",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    token=os.environ["TARGETADS_TOKEN"].strip(); project_id=int(os.environ.get("TARGETADS_PROJECT_ID","").strip() or "12787")
    meta=fast.preclassify_meta(fast.base.load_meta(token,project_id)); projects={}; unknown=0; total_imps=0; total_with=0; recognized=0; no_token=0; token_impressions={token:0 for token in fast.base.TOKEN_CHANNELS}
    start=dt.date.fromisoformat(a.from_date); end=dt.date.fromisoformat(a.to_date); job=fast.base.create_job(token,project_id,start,end); url=fast.base.wait_job(token,project_id,job)
    req=urllib.request.Request(url,headers={"User-Agent":"LevelReachFrequency/chunk-leaf"})
    with urllib.request.urlopen(req,timeout=300) as response:
      with gzip.GzipFile(fileobj=response) as compressed:
       with io.TextIOWrapper(compressed,encoding="utf-8-sig",newline="") as stream:
        for row in csv.DictReader(stream):
            total_imps+=1; pid=str(row.get("InteractionPlacementId") or "").strip(); item=meta.get(pid)
            if item:
                ch=item["channel"]; pname=item["project"]; scope=item["scope"]; channel_token=item["token"]
            else: ch=None; pname="Без объекта"; scope="unassigned"; unknown+=1
            if item and channel_token:
                recognized+=1; token_impressions[channel_token]+=1
            else: no_token+=1
            p=projects.get(pname)
            if p is None: p=project(scope); projects[pname]=p
            b=p["channels"][ch] if ch else p["unclassified"]; b["impressions"]+=1
            device=str(row.get("InteractionDeviceID") or "").strip()
            if device: total_with+=1; b["withDevice"]+=1; b["devices"].add(fast.base.h64(device))
    data={"from":a.from_date,"to":a.to_date,"jobId":job,"projectId":project_id,"placementMetaCount":len(meta),"totalImpressions":total_imps,"totalWithDevice":total_with,"unknownPlacementImpressions":unknown,"recognizedTokenImpressions":recognized,"noRecognizedChannelTokenImpressions":no_token,"tokenImpressions":token_impressions,"projects":projects}
    with open(a.out,"wb") as f: pickle.dump(data,f,protocol=pickle.HIGHEST_PROTOCOL)
    print({"from":a.from_date,"to":a.to_date,"rows":total_imps,"projects":len(projects)})
if __name__=="__main__": main()
