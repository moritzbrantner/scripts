#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import datetime as dt
import fnmatch
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

TIMEOUT = 300
RENOVATE = '{\n  "$schema": "https://docs.renovatebot.com/renovate-schema.json",\n  "extends": ["github>moritzbrantner/coding-agent-conventions"]\n}\n'
EDITORCONFIG = 'root = true\n\n[*]\ncharset = utf-8\nend_of_line = lf\ninsert_final_newline = true\ntrim_trailing_whitespace = true\n\n[*.md]\ntrim_trailing_whitespace = false\n'
CLEAN_DIRS = ("target", "node_modules", "dist", "build", ".next", ".expo", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", "bin", "obj")
COMMANDS = (
    "repo-health", "pr-accept", "pr-stack-status", "fleet-status", "repo-bootstrap", "repo-drift", "workflow-pin",
    "dependency-sync", "duplicate-code-scan", "extract-candidate", "pages-smoke", "pages-inventory", "dogfood-check",
    "changed-only", "ci-reproduce", "toolchain-fingerprint", "rust-workspace-audit", "ts-workspace-audit", "expo-readiness",
    "dotnet-api-audit", "roadmap-next", "stale-work", "issue-from-finding", "release-evidence", "repo-graph",
    "ownership-check", "workspace-clean", "batch-pr", "merge-green", "workspace-doctor", "workspace-run",
    "sync-default-branches", "bootstrap-dev-tools",
)


@dataclass(frozen=True, slots=True)
class Target:
    path: Path
    name: str
    slug: str | None = None
    def display(self) -> str: return self.slug or self.name


@dataclass(frozen=True, slots=True)
class Finding:
    repository: str
    code: str
    severity: str
    message: str
    detail: str | None = None
    path: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    def dict(self) -> dict[str, Any]: return {k: v for k, v in asdict(self).items() if v not in (None, {}, [])}


@dataclass(frozen=True, slots=True)
class Outcome:
    repository: str
    status: str
    summary: str
    findings: tuple[Finding, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    def dict(self) -> dict[str, Any]:
        value = {"repository": self.repository, "status": self.status, "summary": self.summary}
        if self.findings: value["findings"] = [f.dict() for f in self.findings]
        if self.data: value["data"] = self.data
        return value


def run(argv: Sequence[str], cwd: Path | None = None, timeout: int = TIMEOUT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(argv), cwd=cwd, check=False, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(list(argv), 127, "", str(e))
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(list(argv), 124, e.stdout or "", (e.stderr or "") + f"\ntimeout after {timeout}s")


def git(repo: Path, *args: str, timeout: int = 30): return run(["git", "-C", str(repo), *args], timeout=timeout)
def clean(repo: Path) -> bool: return git(repo, "status", "--porcelain=v1").stdout.strip() == ""
def head(repo: Path) -> str | None:
    r = git(repo, "rev-parse", "HEAD"); return r.stdout.strip() if r.returncode == 0 else None
def branch(repo: Path) -> str | None:
    r = git(repo, "branch", "--show-current"); return r.stdout.strip() or None
def root_of(path: Path) -> Path | None:
    r = git(path, "rev-parse", "--show-toplevel"); return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None

def slug_from_remote(remote: str) -> str | None:
    remote = remote.strip().rstrip("/")
    for p in (r"^git@github\.com:(.+/.+)$", r"^ssh://git@github\.com/(.+/.+)$", r"^https?://(?:www\.)?github\.com/(.+/.+)$", r"^git://github\.com/(.+/.+)$"):
        m = re.match(p, remote, re.I)
        if m: return m.group(1).removesuffix(".git")
    return None

def slug(repo: Path) -> str | None:
    r = git(repo, "remote", "get-url", "origin"); return slug_from_remote(r.stdout) if r.returncode == 0 else None

def target(path: Path) -> Target:
    root = root_of(path)
    if not root: raise ValueError(f"{path} is not a Git repository")
    return Target(root, root.name, slug(root))

def discover(start: Path, scope="auto", include: Sequence[str]=(), exclude: Sequence[str]=()) -> list[Target]:
    start = start.expanduser().resolve(); here = root_of(start)
    if scope == "repo" or (scope == "auto" and here):
        if not here: raise ValueError(f"{start} is not inside a Git repository")
        values = [target(here)]
    else:
        values = []
        if not start.is_dir(): raise ValueError(f"workspace root does not exist: {start}")
        for child in sorted(start.iterdir(), key=lambda p: p.name.casefold()):
            if child.is_symlink() or not child.is_dir() or child.name.startswith("."): continue
            r = root_of(child)
            if r and r == child.resolve(): values.append(target(child))
    values = [t for t in values if (not include or any(fnmatch.fnmatch(t.name, x) for x in include)) and not any(fnmatch.fnmatch(t.name, x) for x in exclude)]
    return values

def default_branch(repo: Path) -> str:
    r = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip().startswith("refs/remotes/origin/"): return r.stdout.strip().split("/")[-1]
    for name in ("main", "master"):
        if git(repo, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{name}").returncode == 0: return name
    return branch(repo) or "main"
def divergence(repo: Path) -> tuple[int | None, int | None]:
    r = git(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    try: a, b = map(int, r.stdout.split()); return a, b
    except Exception: return None, None

def load_json(path: Path, default=None):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default

def write_json(path: Path, value: Any): path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True)+"\n", encoding="utf-8")
def finding(t: Target, code: str, severity: str, message: str, **kw): return Finding(t.display(), code, severity, message, **kw)
def executable(name: str) -> bool: return shutil.which(name) is not None


def gh(t: Target, args: Sequence[str], timeout=60):
    if not t.slug: raise RuntimeError("repository has no GitHub origin")
    r = run(["gh", *args, "--repo", t.slug], cwd=t.path, timeout=timeout)
    if r.returncode: raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"gh exited {r.returncode}")
    return r
def gh_json(t: Target, args: Sequence[str], timeout=60): return json.loads(gh(t, args, timeout).stdout)
def gh_api(t: Target, endpoint: str, method="GET", fields: dict[str,str] | None=None):
    cmd = ["gh", "api", endpoint, "--method", method]
    for k,v in (fields or {}).items(): cmd += ["-f", f"{k}={v}"]
    r = run(cmd, cwd=t.path, timeout=90)
    if r.returncode: raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return json.loads(r.stdout) if r.stdout.strip() else None

def open_prs(t: Target): return gh_json(t, ["pr","list","--state","open","--limit","100","--json","number,title,headRefName,baseRefName,isDraft,updatedAt,url"])


def package_scripts(repo: Path) -> tuple[str | None, dict[str,str]]:
    p = load_json(repo/"package.json", {}) or {}; scripts = p.get("scripts", {}) if isinstance(p, dict) else {}
    runner = "bun" if (repo/"bun.lock").exists() or (repo/"bun.lockb").exists() else "pnpm" if (repo/"pnpm-lock.yaml").exists() else "yarn" if (repo/"yarn.lock").exists() else "npm" if (repo/"package.json").exists() else None
    return runner, {str(k):str(v) for k,v in scripts.items()} if isinstance(scripts,dict) else {}
def script_cmd(runner: str, name: str): return [runner,"run",name] if runner != "npm" else ["npm","run",name]
def coding_tooling(repo: Path, args: Sequence[str]) -> list[str] | None:
    if executable("coding-tooling"): return ["coding-tooling", *args]
    cli = repo.parent/"coding-tooling"/"src"/"cli.ts"
    if cli.exists() and executable("bun"): return ["bun", str(cli), *args]
    return None

def health_plan(repo: Path, full=False) -> list[list[str]]:
    ct = coding_tooling(repo, ["run","--tier","full" if full else "fast","--strict","--json"])
    if ct and ((repo/".coding-tooling.json").exists() or executable("coding-tooling")): return [ct]
    plan=[]; runner,scripts=package_scripts(repo)
    if runner:
        for names in (("format:check","format-check"),("lint",),("typecheck","type-check"),("test",),("build",)):
            name=next((x for x in names if x in scripts),None)
            if name and (full or name != "build" or not plan): plan.append(script_cmd(runner,name))
    if (repo/"Cargo.toml").exists(): plan += [["cargo","fmt","--check"],["cargo","clippy","--workspace","--all-targets","--all-features","--","-D","warnings"],["cargo","test","--workspace","--all-features"]]
    dot = next(iter(sorted(repo.glob("*.sln"))),None) or next(iter(sorted(repo.glob("*.csproj"))),None)
    if dot: plan += [["dotnet","build",dot.name,"--nologo"],["dotnet","test",dot.name,"--no-build","--nologo"]]
    if any(repo.glob("*.py")) or (repo/"pyproject.toml").exists():
        plan.append([sys.executable,"-m","compileall","-q","."])
        if (repo/"tests").exists(): plan.append([sys.executable,"-m","unittest","discover","-s","tests","-v"])
    return plan

def run_plan(t: Target, plan: Sequence[Sequence[str]], timeout=600) -> Outcome:
    if not plan: return Outcome(t.display(),"unavailable","no deterministic checks discovered")
    executed=[]
    for cmd in plan:
        r=run(cmd,cwd=t.path,timeout=timeout); executed.append({"command":list(cmd),"returncode":r.returncode})
        if r.returncode:
            f=finding(t,"check-failed","error",f"check exited {r.returncode}",detail=(r.stderr or r.stdout).strip()[-4000:],data={"command":list(cmd)})
            return Outcome(t.display(),"failed","deterministic check failed",(f,),{"checks":executed})
    return Outcome(t.display(),"passed",f"{len(executed)} deterministic checks passed",data={"checks":executed})

