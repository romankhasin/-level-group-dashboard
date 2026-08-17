#!/usr/bin/env python3
"""Process one Target Ads date chunk into exact reach bitmaps at every report level."""
import argparse,csv,gzip,io,pickle,urllib.request
from pyroaring import BitMap64
import export_reach_frequency_july_fast as fast

CHANNELS=fast.base.CHANNEL_ORDER

def bucket(): return {"impressions":0,"withDevice":0,"devices":BitMap64()}
def project_bucket(scope): return {"scope":scope,"total":bucket(),"channels":{c:bucket() for c in CHANNELS},"unclassified":bucket()}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--from-date",required=True); p.add_argument("--to-date",required=True); p.add_argument("--out",required=True); a=p.parse_args()
    import datetime as dt,os
    token=os.environ["TARGETADS_TOKEN"].strip(); project_id=int(os.environ.get("TARGETADS_PROJECT_ID","").strip() or "12787")
    meta=fast.preclassify_meta(fast.base.load_meta(token,project_id))
    total=bucket(); channels={c:bucket() for c in CHANNELS}; unc=bucket(); projects={}; unknown=0
    start=dt.date.fromisoformat(a.from_date); end=dt.date.fromisoformat(a.to_date)
    job=fast.base.create_job(token,project_id,start,end); url=fast.base.wait_job(token,project_id,job)
    req=urllib.request.Request(url,headers={"User-Agent":"LevelReachFrequency/chunk"})
    with urllib.request.urlopen(req,timeout=300) as response:
      with gzip.GzipFile(fileobj=response) as compressed:
       with io.TextIOWrapper(compressed,encoding="utf-8-sig",newline="") as stream:
        reader=csv.DictReader(stream)
        for row in reader:
            total["impressions"]+=1
            pid=str(row.get("InteractionPlacementId") or "").strip(); item=meta.get(pid)
            if item:
                ch=item["channel"]; pname=item["project"]; scope=item["scope"]
            else:
                ch=None; pname="Без объекта"; scope="unassigned"; unknown+=1
            cb=channels[ch] if ch else unc
            cb["impressions"]+=1
            proj=projects.get(pname)
            if proj is None: proj=project_bucket(scope); projects[pname]=proj
            proj["total"]["impressions"]+=1
            pb=proj["channels"][ch] if ch else proj["unclassified"]
            pb["impressions"]+=1
            device=str(row.get("InteractionDeviceID") or "").strip()
            if not device: continue
            h=fast.base.h64(device)
            total["withDevice"]+=1; total["devices"].add(h)
            cb["withDevice"]+=1; cb["devices"].add(h)
            proj["total"]["withDevice"]+=1; proj["total"]["devices"].add(h)
            pb["withDevice"]+=1; pb["devices"].add(h)
    payload={"from":a.from_date,"to":a.to_date,"jobId":job,"projectId":project_id,"placementMetaCount":len(meta),"total":total,"channels":channels,"unclassified":unc,"projects":projects,"unknownPlacementImpressions":unknown}
    with gzip.open(a.out,"wb",compresslevel=1) as f: pickle.dump(payload,f,protocol=pickle.HIGHEST_PROTOCOL)
    print({"from":a.from_date,"to":a.to_date,"impressions":total["impressions"],"reach":len(total["devices"]),"projects":len(projects)})
if __name__=="__main__": main()
