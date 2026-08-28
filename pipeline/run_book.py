#!/usr/bin/env python3
"""Run every chapter of a book through the pipeline, N chapters concurrently.
Usage: run_book.py <en-dir> --out <dir> [--config budget] [--jobs 3] [--glob '1*.md']"""
import argparse,concurrent.futures,glob,json,os,pathlib,subprocess,sys,time
HERE=os.path.dirname(os.path.abspath(__file__))
import re as _re
def one(f,out,config):
    name=_re.sub(r"[^A-Za-z0-9._-]","_",pathlib.Path(f).stem)
    # purge stale artifacts so a failed chapter can never inherit an old run's report
    for suff in ("-report.json","-final.md","-blocks.json"):
        stale=pathlib.Path(out)/f"{name}{suff}"
        if stale.exists(): stale.unlink()
    t0=time.time()
    r=subprocess.run([sys.executable,os.path.join(HERE,"pipeline.py"),"run",f,"--out",out,"--config",config,"--name",name],
                     capture_output=True,text=True)
    ok=r.returncode==0
    print(("OK  " if ok else "FAIL")+f" {name}  {time.time()-t0:.0f}s"+("" if ok else f"\n{r.stdout[-500:]}\n{r.stderr[-500:]}"),flush=True)
    return name,ok
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("en_dir"); ap.add_argument("--out",required=True)
    ap.add_argument("--config",default="budget"); ap.add_argument("--jobs",type=int,default=3)
    ap.add_argument("--glob",default="1*.md")
    a=ap.parse_args()
    # total in-flight cap: divide the per-process concurrency across parallel chapters
    if "PIPELINE_CONCURRENCY" not in os.environ:
        os.environ["PIPELINE_CONCURRENCY"]=str(max(2, 12//max(1,a.jobs)))
    files=sorted(glob.glob(os.path.join(a.en_dir,a.glob)))
    if not files: sys.exit(f"no files match {a.glob} in {a.en_dir}")
    print(f"{len(files)} chapters, {a.jobs} concurrent, config={a.config}")
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        res=list(ex.map(lambda f: one(f,a.out,a.config),files))
    reports=[]; missing=[]
    for f in files:
        p=pathlib.Path(a.out)/f"{_re.sub(r'[^A-Za-z0-9._-]','_',pathlib.Path(f).stem)}-report.json"
        if p.exists(): reports.append(json.load(open(p)))
        else: missing.append(pathlib.Path(f).stem)
    if missing: print(f"WARNING: {len(missing)} chapter(s) produced no report: {missing}")
    ok=sum(1 for _,s in res if s)
    summary={"chapters":len(files),"succeeded":ok,
             "totals":{k:sum(r.get(k,0) for r in reports) for k in ("blocks","changed","kept","reverted","repaired")},
             "residual_rule_issues":sum(len(r.get("residual_rule_issues",[])) for r in reports),
             "seconds":sum(r.get("seconds",0) for r in reports)}
    json.dump(summary,open(pathlib.Path(a.out)/"book-report.json","w"),indent=1)
    print(json.dumps(summary))
    sys.exit(0 if ok==len(files) else 1)
if __name__=="__main__":
    main()
