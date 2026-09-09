#!/usr/bin/env python3
from __future__ import annotations

from repo_ops_remote import *  # noqa: F403

def cmd_clean(ts,a):
    out=[]; candidates=a.candidate or CLEAN_DIRS
    for t in ts:
        removable=[]; skipped=[]
        for name in candidates:
            for p in sorted(t.path.glob(f"**/{name}")):
                if not p.is_dir() or ".git" in p.parts: continue
                if git(t.path,"check-ignore","-q",str(p)).returncode: skipped.append(str(p.relative_to(t.path))); continue
                removable.append(str(p.relative_to(t.path)))
                if a.apply: shutil.rmtree(p)
        out.append(Outcome(t.display(),"changed" if a.apply and removable else "planned" if removable else "clean",f"{'removed' if a.apply else 'would remove'} {len(set(removable))} ignored build/cache directorie(s)",data={"candidates":sorted(set(removable)),"skipped":sorted(set(skipped)),"apply":a.apply}))
    return out

def unresolved_threads(t: Target,n:int):
    owner,name=t.slug.split("/",1); q='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100){nodes{isResolved} pageInfo{hasNextPage}}}}}'
    r=run(["gh","api","graphql","-f",f"query={q}","-f",f"owner={owner}","-f",f"name={name}","-F",f"number={n}"],cwd=t.path,timeout=90)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    p=json.loads(r.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]
    return sum(1 for x in p.get("nodes",[]) if not x.get("isResolved"))+(1 if p.get("pageInfo",{}).get("hasNextPage") else 0)
def pr_state(t: Target,n:int):
    p=gh_json(t,["pr","view",str(n),"--json","number,title,headRefName,baseRefName,headRefOid,mergeStateStatus,reviewDecision,isDraft,url"]); c=run(["gh","pr","checks",str(n),"--required","--json","name,state,bucket,link,workflow","--repo",t.slug],cwd=t.path,timeout=90); checks=json.loads(c.stdout) if c.stdout.strip().startswith("[") else []
    return {**p,"checks":checks,"unresolved":unresolved_threads(t,n)}
def pr_blockers(t: Target,p):
    fs=[]; n=p["number"]
    if p.get("isDraft"): fs.append(finding(t,"pr-draft","error",f"PR #{n} is draft"))
    if not p.get("headRefOid"): fs.append(finding(t,"pr-head-sha-missing","error",f"PR #{n} has no exact head SHA evidence"))
    if str(p.get("mergeStateStatus","UNKNOWN")).upper() in {"BLOCKED","BEHIND","DIRTY","DRAFT","UNKNOWN"}: fs.append(finding(t,"pr-not-merge-ready","error",f"PR #{n} merge state is {p.get('mergeStateStatus')}"))
    if p.get("reviewDecision") in {"CHANGES_REQUESTED","REVIEW_REQUIRED"}: fs.append(finding(t,"review-blocker","error",f"PR #{n} review decision is {p.get('reviewDecision')}"))
    if p.get("unresolved"): fs.append(finding(t,"unresolved-review-threads","error",f"PR #{n} has {p['unresolved']} unresolved/uninspected thread(s)"))
    if not p.get("checks"): fs.append(finding(t,"required-check-evidence-empty","error",f"PR #{n} has no required-check evidence; missing checks are not green"))
    for c in p.get("checks",[]):
        if str(c.get("bucket","")).casefold() not in {"pass","skipping"} and str(c.get("state","")).upper() not in {"SUCCESS","SKIPPED","NEUTRAL"}: fs.append(finding(t,"required-check-not-green","error",f"PR #{n} required check {c.get('name')} is not green",data=c))
    return fs
def pr_numbers(t: Target,explicit): return [explicit] if explicit else [int(x["number"]) for x in open_prs(t)]
def cmd_pr_accept(ts,a,merge=False):
    out=[]
    for t in ts:
        try:
            nums=pr_numbers(t,a.pr)
            if not nums: out.append(Outcome(t.display(),"clean","no open pull requests")); continue
            for n in nums:
                p=pr_state(t,n); fs=pr_blockers(t,p)
                if fs: out.append(Outcome(t.display(),"blocked",f"PR #{n} fails exact-head acceptance",tuple(fs),p)); continue
                if merge and a.apply:
                    payload=gh_api(t,f"repos/{t.slug}/pulls/{n}/merge","PUT",{"sha":p["headRefOid"],"merge_method":a.method})
                    if not payload.get("merged"): raise RuntimeError(payload.get("message","merge failed"))
                    out.append(Outcome(t.display(),"changed",f"merged PR #{n} at exact head {p['headRefOid'][:12]}",data=p))
                else: out.append(Outcome(t.display(),"planned" if merge else "passed",f"PR #{n} {'is eligible to merge' if merge else 'passes exact-head acceptance'}",data=p))
        except Exception as e: out.append(Outcome(t.display(),"error","GitHub PR query/write failed",(finding(t,"github-query-failed","error",str(e)),)))
    return out
def cmd_stack(ts,a):
    out=[]
    for t in ts:
        try:
            rows=open_prs(t); heads={x["headRefName"]:x["number"] for x in rows}; data=[{**x,"parentPr":heads.get(x["baseRefName"]),"stacked":x["baseRefName"] in heads} for x in rows]; out.append(Outcome(t.display(),"ok",f"{len(rows)} open PR(s), {sum(1 for x in data if x['stacked'])} stacked relationship(s)",data={"pullRequests":data}))
        except Exception as e: out.append(Outcome(t.display(),"error","GitHub PR stack query failed",(finding(t,"github-query-failed","error",str(e)),)))
    return out
def cmd_doctor(ts,a):
    out=[]
    for t in ts:
        req={"git"}; runner,_=package_scripts(t.path)
        if runner:req.add(runner)
        if (t.path/"Cargo.toml").exists():req.add("cargo")
        if any(t.path.glob("*.sln")) or any(t.path.glob("*.csproj")):req.add("dotnet")
        if t.slug:req.add("gh")
        missing=sorted(x for x in req if not executable(x)); fs=tuple(finding(t,"required-tool-missing","error",f"required tool missing: {x}") for x in missing); out.append(Outcome(t.display(),"failed" if missing else "passed",f"{len(req)-len(missing)}/{len(req)} required tools available",fs,{"required":sorted(req),"missing":missing}))
    return out
def cmd_workspace_run(ts,a):
    argv=shlex.split(a.run); out=[]
    if not argv: raise ValueError("--run must not be empty")
    for t in ts:
        r=run(argv,cwd=t.path,timeout=a.timeout); fs=() if not r.returncode else (finding(t,"workspace-command-failed","error",f"command exited {r.returncode}",detail=(r.stderr or r.stdout)[-4000:]),); out.append(Outcome(t.display(),"passed" if not r.returncode else "failed",f"exit {r.returncode}: {' '.join(map(shlex.quote,argv))}",fs))
        if r.returncode and a.fail_fast:break
    return out
def cmd_sync_default(ts,a):
    out=[]
    for t in ts:
        db=default_branch(t.path); fs=[]
        if not clean(t.path):fs.append(finding(t,"dirty-worktree","error","refusing to update dirty worktree"))
        if branch(t.path)!=db:fs.append(finding(t,"not-on-default-branch","error",f"checked out branch is not {db}"))
        if fs:out.append(Outcome(t.display(),"blocked","default branch update blocked",tuple(fs)));continue
        if a.apply and git(t.path,"fetch","--prune","origin",timeout=120).returncode:out.append(Outcome(t.display(),"failed","fetch failed"));continue
        r=git(t.path,"rev-list","--left-right","--count",f"HEAD...origin/{db}")
        if r.returncode:out.append(Outcome(t.display(),"unavailable",f"origin/{db} unavailable"));continue
        ahead,behind=map(int,r.stdout.split())
        if ahead:out.append(Outcome(t.display(),"blocked",f"local default branch ahead/diverged by {ahead}"));continue
        if not behind:out.append(Outcome(t.display(),"clean","default branch already current"));continue
        if not a.apply:out.append(Outcome(t.display(),"planned",f"would fast-forward {behind} commit(s)"));continue
        m=git(t.path,"merge","--ff-only",f"origin/{db}",timeout=120);out.append(Outcome(t.display(),"changed" if not m.returncode else "failed",f"fast-forwarded {behind} commit(s)" if not m.returncode else "fast-forward failed"))
    return out
