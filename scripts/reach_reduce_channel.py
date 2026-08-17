#!/usr/bin/env python3
import argparse,gc,pickle
from pathlib import Path
from pyroaring import BitMap64

def bucket(): return {"scope":"unassigned","impressions":0,"withDevice":0,"devices":BitMap64()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--channel",required=True); ap.add_argument("--key",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    projects={}; files=sorted(Path(a.input).rglob("*.pkl"))
    if not files: raise RuntimeError("No chunk artifacts")
    for path in files:
        with open(path,"rb") as f: d=pickle.load(f)
        for name,p in d["projects"].items():
            src=p["unclassified"] if a.channel=="__UNC__" else p["channels"][a.channel]
            if not src["impressions"]: continue
            dst=projects.get(name)
            if dst is None: dst=bucket(); dst["scope"]=p["scope"]; projects[name]=dst
            dst["impressions"]+=src["impressions"]; dst["withDevice"]+=src["withDevice"]; dst["devices"] |= src["devices"]
        del d; gc.collect()
    with open(a.out,"wb") as f: pickle.dump({"key":a.key,"channel":a.channel,"projects":projects},f,protocol=pickle.HIGHEST_PROTOCOL)
    print({"key":a.key,"projects":len(projects),"impressions":sum(x["impressions"] for x in projects.values())})
if __name__=="__main__": main()
