#!/usr/bin/env python3
from __future__ import annotations

from repo_ops_checks import *  # noqa: F403

def duplicate_groups(ts):
    groups=defaultdict(list)
    for t in ts:
        for digest,name,path,start,end,lang in source_functions(t): groups[digest].append((t.display(),name,path,start,end,lang))
    return [v for v in groups.values() if len({(x[0],x[2],x[3]) for x in v})>1]
def cmd_duplicate(ts,a,extract=False):
    groups=duplicate_groups(ts); fs=[]; data=[]
    for g in groups:
        repos={x[0] for x in g}
        if (a.cross_repo_only if hasattr(a,"cross_repo_only") else extract) and len(repos)<2: continue
        owner="shared package after confirming two stable consumers"
        text=" ".join(" ".join(map(str,x)) for x in g).lower()
        if extract:
            if "editor" in text: owner="editor-core or the domain-specific editor owner"
            elif all(x[5]==".rs" for x in g): owner="the lowest Rust foundation with two real consumers"
            elif any(x[5] in {".ts",".tsx",".js",".jsx"} for x in g) and any(k in text for k in ("component","hook","react","ui")): owner="platform-packages"
        row={"repositories":sorted(repos),"locations":[f"{x[0]}:{x[2]}:{x[3]}-{x[4]}" for x in g],"ownerSuggestion":owner if extract else None}; data.append(row); fs.append(Finding("workspace","extraction-candidate" if extract else "duplicate-function","info" if extract else "warn",f"duplicate function group across {len(g)} locations",detail="\n".join(row["locations"]),data=row))
    return [Outcome("workspace","candidates" if extract and fs else "attention" if fs else "clean",f"{len(fs)} {'extraction candidate' if extract else 'duplicate function group'}(s)",tuple(fs),{"groups":data})]

def pages_info(repo: Path):
    wf=[]
    for p in sorted((repo/".github"/"workflows").glob("*.y*ml")) if (repo/".github"/"workflows").exists() else []:
        if re.search(r"actions/(?:upload-pages-artifact|deploy-pages)@|github-pages",p.read_text(encoding="utf-8",errors="replace"),re.I): wf.append(p.relative_to(repo).as_posix())
    runner,scripts=package_scripts(repo); cmd=None
    for name in ("pages:build","build:pages","build:github-pages","pages","build"):
        if runner and name in scripts: cmd=script_cmd(runner,name); break
    if not cmd:
        for d in ("site","docs"):
            if (repo/d/"index.html").is_file(): cmd=[sys.executable,"-c",f"from pathlib import Path; assert Path('{d}/index.html').is_file()"]
    return wf,cmd
def cmd_pages_inventory(ts,a):
    return [Outcome(t.display(),"present" if (w:=pages_info(t.path))[0] else "absent",f"Pages {'configured' if w[0] else 'not configured'}",data={"workflows":w[0],"buildCommand":w[1]}) for t in ts]
def cmd_pages_smoke(ts,a):
    out=[]
    for t in ts:
        wf,cmd=pages_info(t.path)
        if not cmd: out.append(Outcome(t.display(),"unavailable","no local Pages build/surface command discovered",data={"workflows":wf})); continue
        r=run(cmd,cwd=t.path,timeout=a.timeout); fs=() if not r.returncode else (finding(t,"pages-smoke-failed","error",f"Pages smoke exited {r.returncode}",detail=(r.stderr or r.stdout)[-4000:]),); out.append(Outcome(t.display(),"passed" if not r.returncode else "failed","Pages smoke passed" if not r.returncode else "Pages smoke failed",fs,{"command":cmd,"workflows":wf}))
    return out
def cmd_dogfood(ts,a):
    out=[]; browser=next((shutil.which(x) for x in ("chromium","chromium-browser","google-chrome","google-chrome-stable","msedge") if shutil.which(x)),None)
    for t in ts:
        payload=None; source=None; notes=[]
        if t.slug and browser:
            url="https://moritzbrantner.github.io/coding-tooling/run.json/?"+urllib.parse.urlencode({"repo":t.slug,"argv":a.operation}); r=run([browser,"--headless=new","--disable-gpu","--no-sandbox","--virtual-time-budget=10000","--dump-dom",url],cwd=t.path,timeout=90)
            if not r.returncode:
                for m in re.finditer(r"<pre[^>]*>(.*?)</pre>",r.stdout,re.I|re.S):
                    try: payload=json.loads(html.unescape(re.sub(r"<[^>]+>","",m.group(1))).strip()); source="coding-tooling-pages"; break
                    except json.JSONDecodeError: pass
        if payload is None:
            ct=coding_tooling(t.path,shlex.split(a.operation))
            if ct:
                r=run(ct,cwd=t.path,timeout=120)
                if not r.returncode:
                    try: payload=json.loads(r.stdout); source="coding-tooling-local-fallback"
                    except json.JSONDecodeError: notes.append("local coding-tooling did not emit JSON")
        if payload is None: out.append(Outcome(t.display(),"unavailable","dogfood evidence unavailable",(finding(t,"dogfood-check-unavailable","warn","Pages/browser and local coding-tooling evidence unavailable"),))); continue
        artifact=None
        if not a.no_save:
            artifact=t.path/".artifacts"/"repo-ops"/"dogfood"/f"{head(t.path) or 'unknown'}.json"; write_json(artifact,{"source":source,"repository":t.slug,"headSha":head(t.path),"operation":a.operation,"evidence":payload})
        out.append(Outcome(t.display(),str(payload.get("status","passed")) if isinstance(payload,dict) else "passed",f"dogfood evidence from {source}",data={"artifact":str(artifact) if artifact else None,"evidence":payload,"notes":notes}))
    return out

def changed_paths(repo: Path,base=None):
    r=git(repo,"merge-base","HEAD",base or "origin/HEAD")
    if r.returncode: r=git(repo,"rev-parse","HEAD~1")
    if r.returncode: return []
    d=git(repo,"diff","--name-only",f"{r.stdout.strip()}...HEAD"); return sorted(x for x in d.stdout.splitlines() if x)
def capabilities(paths):
    c=set()
    for p in paths:
        q=p.lower()
        if q.startswith(".github/workflows/"): c|={"workflow-validation","repo-health"}
        if q.endswith((".ts",".tsx",".js",".jsx")): c|={"lint","typecheck","test"}
        if q.endswith(".rs") or q.endswith(("cargo.toml","cargo.lock")): c|={"format:check","lint","test"}
        if q.endswith((".cs",".csproj",".sln")): c|={"build","test"}
        if q.endswith((".py",".pyi")): c|={"python:compile","test"}
        if "pages" in q or q.startswith(("site/","docs/")): c.add("pages-smoke")
    return sorted(c)
def cmd_changed(ts,a):
    out=[]
    for t in ts:
        paths=changed_paths(t.path,a.base); data={"changedPaths":paths,"capabilities":capabilities(paths)}; ct=coding_tooling(t.path,["affected",*(["--base",a.base] if a.base else []),"--json"])
        if ct:
            r=run(ct,cwd=t.path,timeout=90)
            if not r.returncode:
                try:data["codingToolingAffected"]=json.loads(r.stdout)
                except json.JSONDecodeError:pass
        out.append(Outcome(t.display(),"planned",f"{len(paths)} changed path(s), {len(data['capabilities'])} capability set(s)",data=data))
    return out
