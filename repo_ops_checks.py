#!/usr/bin/env python3
from __future__ import annotations

from repo_ops_base import *  # noqa: F403

def cmd_repo_health(ts,a): return [run_plan(t,health_plan(t.path,a.full),a.timeout) for t in ts]
def cmd_fleet_status(ts,a):
    out=[]
    for t in ts:
        aa,bb=divergence(t.path); fs=[]
        if not clean(t.path): fs.append(finding(t,"dirty-worktree","warn","working tree has local changes"))
        if bb: fs.append(finding(t,"upstream-behind","warn",f"branch is {bb} commit(s) behind upstream"))
        data={"path":str(t.path),"branch":branch(t.path),"defaultBranch":default_branch(t.path),"headSha":head(t.path),"clean":clean(t.path),"ahead":aa,"behind":bb}
        if a.github and executable("gh") and t.slug:
            try:
                data["openPullRequests"]=len(open_prs(t)); runs=gh_json(t,["run","list","--limit","1","--json","databaseId,status,conclusion,workflowName,url"]); data["latestRun"]=runs[0] if runs else None
            except Exception as e: fs.append(finding(t,"github-status-unavailable","warn",str(e)))
        out.append(Outcome(t.display(),"attention" if fs else "clean",f"{data['branch'] or 'detached'} at {(data['headSha'] or '')[:12]}",tuple(fs),data))
    return out
def cmd_repo_bootstrap(ts,a):
    out=[]
    for t in ts:
        created=[]; fs=[]
        for rel,content in ((".editorconfig",EDITORCONFIG),("renovate.json",RENOVATE)):
            p=t.path/rel
            if p.exists(): continue
            if a.apply: p.write_text(content,encoding="utf-8"); created.append(rel)
            else: fs.append(finding(t,"bootstrap-missing","info",f"would create {rel}",path=rel))
        out.append(Outcome(t.display(),"changed" if created else "planned" if fs else "clean",f"{len(created)} created, {len(fs)} planned",tuple(fs),{"created":created,"apply":a.apply}))
    return out
def cmd_repo_drift(ts,a):
    out=[]
    for t in ts:
        fs=[]; p=load_json(t.path/"renovate.json",None)
        if not isinstance(p,dict): fs.append(finding(t,"renovate-missing","warn","renovate.json missing or invalid",path="renovate.json"))
        elif "github>moritzbrantner/coding-agent-conventions" not in p.get("extends",[]): fs.append(finding(t,"renovate-extends-drift","warn","Renovate does not extend shared coding-agent-conventions",path="renovate.json"))
        if a.baseline:
            base=a.baseline.expanduser().resolve()
            for rel in (".editorconfig","renovate.json",".coding-tooling.json"):
                s=base/rel; d=t.path/rel
                if s.is_file() and (not d.is_file() or hashlib.sha256(s.read_bytes()).digest()!=hashlib.sha256(d.read_bytes()).digest()): fs.append(finding(t,"baseline-drift","warn",f"{rel} differs from baseline",path=rel))
        out.append(Outcome(t.display(),"drift" if fs else "clean",f"{len(fs)} drift finding(s)",tuple(fs)))
    return out
def workflow_findings(repo: Path):
    found=[]; sha=re.compile(r"^[0-9a-f]{40}$",re.I); rx=re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
    for p in sorted((repo/".github"/"workflows").glob("*.y*ml")) if (repo/".github"/"workflows").exists() else []:
        for n,line in enumerate(p.read_text(encoding="utf-8",errors="replace").splitlines(),1):
            m=rx.search(line)
            if not m: continue
            value=m.group(1).strip("'\"")
            if value.startswith(("./","docker://")) or "@" not in value: continue
            ref=value.rsplit("@",1)[1]
            if not sha.fullmatch(ref): found.append((p.relative_to(repo).as_posix(),n,value,ref))
    return found
def cmd_workflow_pin(ts,a):
    out=[]
    for t in ts:
        rows=workflow_findings(t.path); fs=tuple(finding(t,"workflow-ref-not-sha-pinned","warn",f"{v} is pinned to mutable ref {r}",path=f"{p}:{n}") for p,n,v,r in rows); out.append(Outcome(t.display(),"attention" if fs else "clean",f"{len(fs)} unpinned workflow reference(s)",fs))
    return out

def dependency_versions(ts):
    allv=defaultdict(list)
    for t in ts:
        p=load_json(t.path/"package.json",{}) or {}
        if isinstance(p,dict):
            for sec in ("dependencies","devDependencies","peerDependencies"):
                for name,ver in (p.get(sec,{}) or {}).items():
                    if isinstance(ver,str): allv[f"npm:{name}"].append((t.display(),ver))
        cargo=t.path/"Cargo.toml"
        if cargo.exists():
            sec=""
            for line in cargo.read_text(encoding="utf-8",errors="replace").splitlines():
                s=line.strip()
                if s.startswith("["): sec=s.strip("[]"); continue
                if sec not in {"dependencies","dev-dependencies","build-dependencies","workspace.dependencies"}: continue
                m=re.match(r"([\w-]+)\s*=\s*(?:\"([^\"]+)\"|\{[^}]*version\s*=\s*\"([^\"]+)\")",s)
                if m: allv[f"cargo:{m.group(1)}"].append((t.display(),m.group(2) or m.group(3)))
    return allv
def cmd_dependency_sync(ts,a):
    versions=dependency_versions(ts); fs=[]; drift={}
    for dep,rows in versions.items():
        by=defaultdict(list)
        for repo,ver in rows: by[ver].append(repo)
        if len(by)>1 and len({x[0] for x in rows})>1: drift[dep]={k:sorted(v) for k,v in by.items()}; fs.append(Finding("workspace","dependency-version-drift","warn",f"{dep} has {len(by)} versions",data={"versions":drift[dep]}))
    return [Outcome("workspace","attention" if fs else "clean",f"{len(fs)} shared dependency drift(s)",tuple(fs),{"drift":drift})]

def source_functions(t: Target):
    excluded={".git","node_modules","target","dist","build","coverage","vendor","fixtures"}; result=[]
    for root,dirs,files in os.walk(t.path):
        dirs[:]=[d for d in dirs if d not in excluded and not d.startswith(".")]
        for name in files:
            p=Path(root)/name
            if p.suffix not in {".py",".rs",".ts",".tsx",".js",".jsx",".cs"}: continue
            try: text=p.read_text(encoding="utf-8",errors="replace")
            except OSError: continue
            if p.suffix==".py":
                try: tree=ast.parse(text)
                except SyntaxError: continue
                lines=text.splitlines()
                for n in ast.walk(tree):
                    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and getattr(n,"end_lineno",None):
                        block="\n".join(x.strip() for x in lines[n.lineno-1:n.end_lineno] if x.strip() and not x.lstrip().startswith("#"))
                        if len(block.splitlines())>=4: result.append((hashlib.sha256(block.encode()).hexdigest(),n.name,p.relative_to(t.path).as_posix(),n.lineno,n.end_lineno,p.suffix))
            else:
                rx=re.compile(r"^\s*(?:export\s+|pub(?:\([^)]*\))?\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|async\s+)*(?:function\s+|fn\s+|[\w<>,?\[\].]+\s+)([A-Za-z_$][\w$]*)\s*[<(]")
                lines=text.splitlines(); i=0
                while i<len(lines):
                    m=rx.search(lines[i])
                    if not m: i+=1; continue
                    depth=0; seen=False; end=i
                    for j in range(i,min(len(lines),i+400)):
                        line=re.sub(r'"(?:\\.|[^"\\])*"','""',lines[j]); depth+=line.count("{")-line.count("}"); seen|="{" in line; end=j
                        if seen and depth<=0: break
                    block="\n".join(x.strip() for x in lines[i:end+1] if x.strip() and not x.lstrip().startswith("//"))
                    if seen and len(block.splitlines())>=4: result.append((hashlib.sha256(block.encode()).hexdigest(),m.group(1),p.relative_to(t.path).as_posix(),i+1,end+1,p.suffix))
                    i=max(i+1,end+1)
    return result
