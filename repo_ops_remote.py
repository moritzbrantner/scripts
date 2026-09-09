#!/usr/bin/env python3
from __future__ import annotations

from repo_ops_analysis import *  # noqa: F403

def cmd_ci_reproduce(ts,a):
    out=[]
    for t in ts:
        try:
            runs=gh_json(t,["run","list","--limit","20","--json","databaseId,headSha,status,conclusion,workflowName,url"]); selected=next((x for x in runs if int(x["databaseId"])==a.run_id),None) if a.run_id else next((x for x in runs if x.get("conclusion") not in {None,"success","neutral","skipped"}),None)
            if not selected: out.append(Outcome(t.display(),"clean","no failed workflow run found")); continue
            jobs=gh_json(t,["run","view",str(selected["databaseId"]),"--json","jobs"]).get("jobs",[]); failed=[]
            for job in jobs:
                failed += [{"job":job.get("name"),"step":s.get("name"),"conclusion":s.get("conclusion")} for s in job.get("steps",[]) if s.get("conclusion") in {"failure","cancelled","timed_out","action_required"}]
            fs=() if failed else (finding(t,"failed-step-unavailable","warn","workflow failed before a failing executed step was reported"),); out.append(Outcome(t.display(),"planned",f"{len(failed)} failed step(s)",fs,{"run":selected,"failedSteps":failed,"localPlan":health_plan(t.path,True)}))
        except Exception as e: out.append(Outcome(t.display(),"error","GitHub CI query failed",(finding(t,"github-query-failed","error",str(e)),)))
    return out
def fingerprint(t: Target):
    tools={}
    for name,cmd in (("git",["git","--version"]),("python",[sys.executable,"--version"]),("gh",["gh","--version"]),("bun",["bun","--version"]),("node",["node","--version"]),("cargo",["cargo","--version"]),("rustc",["rustc","--version"]),("dotnet",["dotnet","--version"])):
        r=run(cmd,timeout=20); tools[name]={"available":r.returncode!=127,"returncode":r.returncode,"version":(r.stdout or r.stderr).splitlines()[0] if (r.stdout or r.stderr).splitlines() else None}
    locks={n:hashlib.sha256((t.path/n).read_bytes()).hexdigest() for n in ("bun.lock","bun.lockb","package-lock.json","pnpm-lock.yaml","yarn.lock","Cargo.lock","packages.lock.json","global.json") if (t.path/n).is_file()}
    return {"repository":t.display(),"path":str(t.path),"headSha":head(t.path),"branch":branch(t.path),"defaultBranch":default_branch(t.path),"clean":clean(t.path),"tools":tools,"lockfiles":locks}
def cmd_fingerprint(ts,a): return [Outcome(t.display(),"ok","toolchain fingerprint collected",data=fingerprint(t)) for t in ts]
def audit_plan(repo: Path,kind):
    if kind=="rust": return [["cargo","fmt","--check"],["cargo","clippy","--workspace","--all-targets","--all-features","--","-D","warnings"],["cargo","test","--workspace","--all-features"],["cargo","package","--workspace","--allow-dirty","--no-verify"]] if (repo/"Cargo.toml").exists() else []
    runner,scripts=package_scripts(repo)
    if kind=="ts" and runner:
        return [script_cmd(runner,n) for n in ("format:check","lint","typecheck","test","storybook:check","build") if n in scripts]
    p=load_json(repo/"package.json",{}) or {}; deps={**(p.get("dependencies",{}) or {}),**(p.get("devDependencies",{}) or {})} if isinstance(p,dict) else {}
    if kind=="expo" and "expo" in deps:
        doctor=["bun","x","expo-doctor"] if runner=="bun" else ["npx","expo-doctor"]; return [doctor]+[script_cmd(runner or "npm",n) for n in ("typecheck","test","build:web","web:build") if n in scripts]
    dot=next(iter(sorted(repo.glob("*.sln"))),None) or next(iter(sorted(repo.glob("*.csproj"))),None)
    if kind=="dotnet" and dot: return [["dotnet","restore",dot.name,"--locked-mode"],["dotnet","build",dot.name,"--no-restore","--nologo","-warnaserror"],["dotnet","test",dot.name,"--no-build","--nologo"]]
    return []
def cmd_audit(ts,a,kind): return [run_plan(t,audit_plan(t.path,kind),a.timeout) if audit_plan(t.path,kind) else Outcome(t.display(),"not-applicable",f"{kind} stack not detected") for t in ts]
def cmd_roadmap(ts,a):
    out=[]; score={"priority:critical":0,"p0":0,"priority:high":1,"p1":1,"p2":2,"next":2,"ready":2}
    for t in ts:
        try:
            rows=gh_json(t,["issue","list","--state","open","--limit","100","--json","number,title,updatedAt,url,labels"]); rows.sort(key=lambda x:(min((score.get(str(l.get('name','')).casefold(),10) for l in x.get('labels',[])),default=10),int(x['number']))); out.append(Outcome(t.display(),"ok",f"selected {min(a.limit,len(rows))} roadmap candidate(s)",data={"issues":rows[:a.limit],"ranking":"explicit priority labels then issue number"}))
        except Exception as e: out.append(Outcome(t.display(),"error","GitHub issue query failed",(finding(t,"github-query-failed","error",str(e)),)))
    return out
def cmd_stale(ts,a):
    cutoff=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=a.days); out=[]
    for t in ts:
        items=[]
        try:
            for kind in ("pr","issue"):
                fields="number,title,updatedAt,url,labels"+(",isDraft,headRefName,baseRefName" if kind=="pr" else ""); rows=gh_json(t,[kind,"list","--state","open","--limit","100","--json",fields])
                for x in rows:
                    try: updated=dt.datetime.fromisoformat(x["updatedAt"].replace("Z","+00:00"))
                    except ValueError: continue
                    if updated<cutoff: items.append({"kind":kind,**x})
            out.append(Outcome(t.display(),"attention" if items else "clean",f"{len(items)} stale open item(s)",data={"items":items,"cutoff":cutoff.isoformat()}))
        except Exception as e: out.append(Outcome(t.display(),"error","GitHub stale-work query failed",(finding(t,"github-query-failed","error",str(e)),)))
    return out
def cmd_issue_from_finding(ts,a):
    p=load_json(a.finding_file,None); raw=[]
    if isinstance(p,list):
        for x in p:
            if isinstance(x,dict) and isinstance(x.get("findings"),list): raw+=x["findings"]
            elif isinstance(x,dict) and "code" in x: raw.append(x)
    elif isinstance(p,dict): raw=p.get("findings",[p] if "code" in p else [])
    out=[]
    for t in ts:
        match=[x for x in raw if str(x.get("repository") or t.display()) in {t.display(),t.name,t.slug}]; planned=[]; created=[]
        try:
            for x in match:
                title=f"[{x.get('code','finding')}] {x.get('message',x.get('code','finding'))}"[:240]; planned.append(title)
                if a.apply:
                    existing=gh_json(t,["issue","list","--state","all","--limit","100","--search",f'"{title}" in:title',"--json","title"])
                    if any(y.get("title")==title for y in existing): continue
                    r=gh(t,["issue","create","--title",title,"--body","Generated from deterministic repository finding.\n\n```json\n"+json.dumps(x,indent=2,sort_keys=True)+"\n```"]); created.append(r.stdout.strip())
            out.append(Outcome(t.display(),"changed" if created else "planned" if planned else "clean",f"{len(created)} created, {len(planned)} candidate(s)",data={"created":created,"planned":planned,"apply":a.apply}))
        except Exception as e: out.append(Outcome(t.display(),"error","issue creation/query failed",(finding(t,"github-write-failed","error",str(e)),)))
    return out
def cmd_release(ts,a):
    out=[]
    for t in ts:
        val=run_plan(t,health_plan(t.path,True),a.timeout) if a.run_validation else None; evidence={"schemaVersion":1,"generatedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"repository":t.display(),"fingerprint":fingerprint(t),"validationPlan":health_plan(t.path,True),"validation":val.dict() if val else None}; base=a.output_root.expanduser().resolve() if a.output_root else t.path/".artifacts"/"repo-ops"/"release-evidence"; path=base/f"{t.name}-{head(t.path) or 'unknown'}.json" if a.output_root else base/f"{head(t.path) or 'unknown'}.json"; write_json(path,evidence); out.append(Outcome(t.display(),"failed" if val and val.status=="failed" else "written",f"release evidence written to {path}",val.findings if val else (),{"artifact":str(path)}))
    return out
def graph_data(ts):
    names={t.name:t.display() for t in ts}; edges=set()
    for t in ts:
        p=load_json(t.path/"package.json",{}) or {}
        if isinstance(p,dict):
            for sec in ("dependencies","devDependencies","peerDependencies"):
                for name,val in (p.get(sec,{}) or {}).items():
                    if name in names: edges.add((t.display(),names[name],f"npm:{sec}"))
                    if isinstance(val,str) and val.startswith(("file:","link:")):
                        c=(t.path/val.split(":",1)[1]).resolve()
                        if c.parent==t.path.parent and c.name in names: edges.add((t.display(),names[c.name],f"npm:{sec}:path"))
        cargo=t.path/"Cargo.toml"
        if cargo.exists():
            for m in re.finditer(r"(?:path|git)\s*=\s*\"([^\"]+)\"",cargo.read_text(encoding="utf-8",errors="replace")):
                v=m.group(1)
                if v.startswith("../"):
                    c=(t.path/v).resolve()
                    if c.parent==t.path.parent and c.name in names: edges.add((t.display(),names[c.name],"cargo:path"))
        w=t.path/".github"/"workflows"
        if w.exists():
            for f in w.glob("*.y*ml"):
                for m in re.finditer(r"uses:\s*([\w.-]+/[\w.-]+)/\.github/workflows/[^@\s]+@",f.read_text(encoding="utf-8",errors="replace")): edges.add((t.display(),m.group(1),"github-actions"))
    return {"nodes":sorted({t.display() for t in ts}|{e[1] for e in edges}),"edges":[{"from":x,"to":y,"kind":k} for x,y,k in sorted(edges)]}
def cmd_graph(ts,a):
    g=graph_data(ts)
    if a.output: write_json(a.output.expanduser().resolve(),g)
    if a.dot:
        lines=["digraph repositories {","  rankdir=LR;"]+[f'  "{n}";' for n in g["nodes"]]+[f'  "{e["from"]}" -> "{e["to"]}" [label="{e["kind"]}"];' for e in g["edges"]]+["}"]; a.dot.expanduser().resolve().write_text("\n".join(lines)+"\n",encoding="utf-8")
    return [Outcome("workspace","ok",f"{len(g['nodes'])} node(s), {len(g['edges'])} edge(s)",data=g)]
def cmd_ownership(ts,a):
    g=graph_data(ts); fs=[]
    for e in g["edges"]:
        if e["kind"].endswith(":path"): fs.append(Finding(e["from"],"sibling-path-dependency","warn",f"direct sibling dependency on {e['to']} couples local working trees",data=e))
    return [Outcome("workspace","attention" if fs else "clean",f"{len(fs)} ownership finding(s)",tuple(fs),{"graph":g})]
