#!/usr/bin/env python3
"""Fleet-capable repository operations CLI."""
from __future__ import annotations

from repo_ops_mutation import *  # noqa: F403

def cmd_batch(ts,a):
    argv=shlex.split(a.run); out=[]
    if not argv:raise ValueError("--run must not be empty")
    for t in ts:
        if not clean(t.path):out.append(Outcome(t.display(),"blocked","batch PR requires clean worktree"));continue
        db=default_branch(t.path)
        if not a.apply:out.append(Outcome(t.display(),"planned",f"would run command and open {a.branch} -> {db}",data={"command":argv}));continue
        original=branch(t.path)
        try:
            r=git(t.path,"switch","-c",a.branch,f"origin/{db}");
            if r.returncode:raise RuntimeError(r.stderr)
            r=run(argv,cwd=t.path,timeout=a.timeout)
            if r.returncode:raise RuntimeError((r.stderr or r.stdout)[-2000:])
            if not git(t.path,"status","--porcelain=v1").stdout.strip():out.append(Outcome(t.display(),"clean","command produced no changes"));continue
            for args in (("add","-A"),("commit","-m",a.title),("push","-u","origin",a.branch)):
                r=git(t.path,*args,timeout=180)
                if r.returncode:raise RuntimeError(r.stderr)
            u=gh(t,["pr","create","--title",a.title,"--body",a.body,"--base",db]).stdout.strip();out.append(Outcome(t.display(),"changed","batch PR created",data={"url":u}))
        except Exception as e:out.append(Outcome(t.display(),"error","batch PR failed",(finding(t,"batch-pr-failed","error",str(e)),)))
        finally:
            if original and clean(t.path):git(t.path,"switch",original)
    return out


def add_scope(p):
    p.add_argument("--root",type=Path,default=Path.cwd());p.add_argument("--scope",choices=("auto","repo","fleet"),default="auto");p.add_argument("--include",action="append",default=[]);p.add_argument("--exclude",action="append",default=[]);p.add_argument("--json",action="store_true")
def parser():
    p=argparse.ArgumentParser(prog="repo-ops",description="Deterministic single-repository and parent-folder fleet operations.");s=p.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        q=s.add_parser(name);add_scope(q)
        if name in {"repo-health"}:q.add_argument("--full",action="store_true");q.add_argument("--timeout",type=int,default=600)
        if name in {"pages-smoke"}:q.add_argument("--timeout",type=int,default=600)
        if name in {"rust-workspace-audit","ts-workspace-audit","expo-readiness","dotnet-api-audit"}:q.add_argument("--timeout",type=int,default=900)
        if name in {"pr-accept","merge-green"}:q.add_argument("--pr",type=int)
        if name=="merge-green":q.add_argument("--apply",action="store_true");q.add_argument("--method",choices=("squash","merge","rebase"),default="squash")
        if name=="fleet-status":q.add_argument("--github",action="store_true")
        if name in {"repo-bootstrap","issue-from-finding","workspace-clean","sync-default-branches"}:q.add_argument("--apply",action="store_true")
        if name=="repo-drift":q.add_argument("--baseline",type=Path)
        if name=="duplicate-code-scan":q.add_argument("--cross-repo-only",action="store_true")
        if name=="dogfood-check":q.add_argument("--operation",default="findings --json");q.add_argument("--no-save",action="store_true")
        if name=="changed-only":q.add_argument("--base")
        if name=="ci-reproduce":q.add_argument("--run-id",type=int)
        if name=="roadmap-next":q.add_argument("--limit",type=int,default=5)
        if name=="stale-work":q.add_argument("--days",type=int,default=7)
        if name=="issue-from-finding":q.add_argument("finding_file",type=Path)
        if name=="release-evidence":q.add_argument("--output-root",type=Path);q.add_argument("--run-validation",action="store_true");q.add_argument("--timeout",type=int,default=900)
        if name=="repo-graph":q.add_argument("--output",type=Path);q.add_argument("--dot",type=Path)
        if name=="workspace-clean":q.add_argument("--candidate",action="append",default=[])
        if name in {"workspace-run","batch-pr"}:q.add_argument("--run",required=True);q.add_argument("--timeout",type=int,default=900 if name=="batch-pr" else 600)
        if name=="workspace-run":q.add_argument("--fail-fast",action="store_true")
        if name=="batch-pr":q.add_argument("--branch",required=True);q.add_argument("--title",required=True);q.add_argument("--body",default="Automated fleet-wide deterministic change.");q.add_argument("--apply",action="store_true")
    return p

def execute(a):
    ts=discover(a.root,a.scope,a.include,a.exclude)
    if not ts:raise ValueError(f"no repositories discovered under {a.root}")
    table={"repo-health":cmd_repo_health,"pr-accept":lambda t,x:cmd_pr_accept(t,x),"pr-stack-status":cmd_stack,"fleet-status":cmd_fleet_status,"repo-bootstrap":cmd_repo_bootstrap,"repo-drift":cmd_repo_drift,"workflow-pin":cmd_workflow_pin,"dependency-sync":cmd_dependency_sync,"duplicate-code-scan":lambda t,x:cmd_duplicate(t,x),"extract-candidate":lambda t,x:cmd_duplicate(t,x,True),"pages-smoke":cmd_pages_smoke,"pages-inventory":cmd_pages_inventory,"dogfood-check":cmd_dogfood,"changed-only":cmd_changed,"ci-reproduce":cmd_ci_reproduce,"toolchain-fingerprint":cmd_fingerprint,"rust-workspace-audit":lambda t,x:cmd_audit(t,x,"rust"),"ts-workspace-audit":lambda t,x:cmd_audit(t,x,"ts"),"expo-readiness":lambda t,x:cmd_audit(t,x,"expo"),"dotnet-api-audit":lambda t,x:cmd_audit(t,x,"dotnet"),"roadmap-next":cmd_roadmap,"stale-work":cmd_stale,"issue-from-finding":cmd_issue_from_finding,"release-evidence":cmd_release,"repo-graph":cmd_graph,"ownership-check":cmd_ownership,"workspace-clean":cmd_clean,"batch-pr":cmd_batch,"merge-green":lambda t,x:cmd_pr_accept(t,x,True),"workspace-doctor":cmd_doctor,"workspace-run":cmd_workspace_run,"sync-default-branches":cmd_sync_default,"bootstrap-dev-tools":cmd_doctor}
    return table[a.command](ts,a)
def print_out(out,json_mode=False):
    if json_mode:print(json.dumps([x.dict() for x in out],indent=2,sort_keys=True))
    else:
        for x in out:
            print(f"[{x.status}] {x.repository}: {x.summary}")
            for f in x.findings:print(f"  {f.severity.upper():5} {f.code}{' ('+f.path+')' if f.path else ''}: {f.message}")
    return 1 if any(x.status in {"failed","blocked","error"} for x in out) else 0
def main(argv: Sequence[str] | None=None):
    p=parser();a=p.parse_args(argv)
    try:return print_out(execute(a),a.json)
    except (ValueError,OSError) as e:print(f"error: {e}",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
