#!/usr/bin/env python3
import csv,gzip,io,urllib.request
import export_reach_frequency_july_fast as fast

def process_fast2(url,meta,projects,counters):
    rows=0; with_device=0
    req=urllib.request.Request(url,headers={"User-Agent":"LevelReachFrequency/fast2"})
    with urllib.request.urlopen(req,timeout=300) as response:
      with gzip.GzipFile(fileobj=response) as compressed:
       with io.TextIOWrapper(compressed,encoding="utf-8-sig",newline="") as stream:
        reader=csv.DictReader(stream)
        for row in reader:
            rows+=1; counters["impressions"]+=1
            pid=str(row.get("InteractionPlacementId") or "").strip(); item=meta.get(pid)
            if item:
                source=item["source"]; channel=item["channel"]; pname=item["project"]; scope=item["scope"]
            else:
                source=""; channel=None; pname="Без объекта"; scope="unassigned"; counters["unknownPlacementImpressions"]+=1
            project=projects.get(pname)
            if project is None:
                project=fast.base.project_bucket(scope); projects[pname]=project
            bucket=project["channels"][channel] if channel else project["unclassified"]
            bucket["impressions"]+=1
            if source: bucket["sources"].add(source)
            device=str(row.get("InteractionDeviceID") or "").strip()
            if device:
                with_device+=1; counters["impressionsWithDevice"]+=1; bucket["withDevice"]+=1; bucket["devices"].add(fast.base.h64(device))
    return {"rows":rows,"rowsWithDevice":with_device}

fast.process_fast=process_fast2
fast.main()
