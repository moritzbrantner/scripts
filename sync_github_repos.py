#!/usr/bin/env python3
"""Clone repositories owned by a GitHub user into one workspace.

The default workspace is the parent directory of this repository, so when this
script lives at ~/dev/scripts/sync_github_repos.py it manages sibling folders
such as ~/dev/audio-analysis and ~/dev/ecs-lab.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

GITHUB_API = "https://api.github.com"
GITHUB_HOST = "github.com"
COMMAND_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Repository:
    name: str
    full_name: str
    clone_url: str
    ssh_url: str
    archived: bool

    def clone_url_for(self, protocol: str) -> str:
        return self.ssh_url if protocol == "ssh" else self.clone_url


@dataclass(frozen=True, slots=True)
class SyncResult:
    repository: str
    status: str
    detail: str


class GitHubError(RuntimeError):
    pass


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            "",
            f"command timed out after {COMMAND_TIMEOUT_SECONDS} seconds",
        )


def resolve_token() -> str | None:
    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(variable)
        if token:
            return token.strip()

    if shutil.which("gh"):
        result = run_command(["gh", "auth", "token"])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

    return None


def github_request(url: str, token: str | None) -> tuple[object, dict[str, str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "moritzbrantner-scripts/repo-sync",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload, dict(response.headers.items())
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("message", body)
        except json.JSONDecodeError:
            message = body
        raise GitHubError(f"GitHub API returned HTTP {error.code}: {message}") from error
    except urllib.error.URLError as error:
        raise GitHubError(f"Could not reach GitHub: {error.reason}") from error


def authenticated_login(token: str) -> str:
    payload, _ = github_request(f"{GITHUB_API}/user", token)
    if not isinstance(payload, dict) or not isinstance(payload.get("login"), str):
        raise GitHubError("GitHub returned an unexpected response for the authenticated user")
    return payload["login"]


def infer_owner_from_repo(repo_dir: Path) -> str | None:
    result = run_command(["git", "-C", str(repo_dir), "remote", "get-url", "origin"])
    if result.returncode != 0:
        return None
    slug = repo_slug_from_remote(result.stdout.strip())
    if slug is None:
        return None
    return slug.split("/", 1)[0]


def resolve_owner(explicit_owner: str | None, token: str | None, repo_dir: Path) -> str:
    if explicit_owner:
        return explicit_owner

    env_owner = os.environ.get("GITHUB_USER")
    if env_owner:
        return env_owner

    inferred = infer_owner_from_repo(repo_dir)
    if inferred:
        return inferred

    if token:
        return authenticated_login(token)

    raise GitHubError(
        "Could not determine the GitHub owner. Pass --owner, set GITHUB_USER, "
        "or run the script from a clone with a GitHub origin."
    )


def iter_repositories(owner: str, token: str | None) -> Iterable[Repository]:
    page = 1
    while True:
        if token:
            query = urllib.parse.urlencode(
                {
                    "affiliation": "owner",
                    "per_page": 100,
                    "page": page,
                    "sort": "full_name",
                    "direction": "asc",
                }
            )
            url = f"{GITHUB_API}/user/repos?{query}"
        else:
            encoded_owner = urllib.parse.quote(owner, safe="")
            query = urllib.parse.urlencode(
                {
                    "type": "owner",
                    "per_page": 100,
                    "page": page,
                    "sort": "full_name",
                    "direction": "asc",
                }
            )
            url = f"{GITHUB_API}/users/{encoded_owner}/repos?{query}"

        payload, _ = github_request(url, token)
        if not isinstance(payload, list):
            raise GitHubError("GitHub returned an unexpected repository-list response")

        matching = [item for item in payload if repository_owner(item).casefold() == owner.casefold()]
        for item in matching:
            yield parse_repository(item)

        if len(payload) < 100:
            break
        page += 1


def repository_owner(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    owner = payload.get("owner")
    if not isinstance(owner, dict):
        return ""
    login = owner.get("login")
    return login if isinstance(login, str) else ""


def parse_repository(payload: object) -> Repository:
    if not isinstance(payload, dict):
        raise GitHubError("GitHub returned a malformed repository entry")

    required = ("name", "full_name", "clone_url", "ssh_url")
    values: dict[str, str] = {}
    for key in required:
        value = payload.get(key)
        if not isinstance(value, str):
            raise GitHubError(f"GitHub repository entry is missing {key}")
        values[key] = value

    return Repository(
        name=values["name"],
        full_name=values["full_name"],
        clone_url=values["clone_url"],
        ssh_url=values["ssh_url"],
        archived=bool(payload.get("archived", False)),
    )


def repo_slug_from_remote(remote: str) -> str | None:
    remote = remote.strip().rstrip("/")
    patterns = (
        rf"^git@{re.escape(GITHUB_HOST)}:(?P<slug>[^/]+/[^/]+)$",
        rf"^ssh://git@{re.escape(GITHUB_HOST)}/(?P<slug>[^/]+/[^/]+)$",
        rf"^https?://{re.escape(GITHUB_HOST)}/(?P<slug>[^/]+/[^/]+)$",
        rf"^git://{re.escape(GITHUB_HOST)}/(?P<slug>[^/]+/[^/]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, remote, flags=re.IGNORECASE)
        if match:
            return match.group("slug").removesuffix(".git")
    return None


def default_workspace_root(script_path: Path) -> Path:
    return script_path.resolve().parent.parent


def sync_repository(
    repository: Repository,
    root: Path,
    *,
    protocol: str,
    fetch: bool,
    dry_run: bool,
) -> SyncResult:
    target = root / repository.name

    if target.exists():
        if target.is_symlink() or not target.is_dir():
            return SyncResult(
                repository.full_name,
                "blocked",
                f"{target} exists and is not an ordinary directory",
            )

        origin = run_command(["git", "-C", str(target), "remote", "get-url", "origin"])
        if origin.returncode != 0:
            return SyncResult(repository.full_name, "blocked", f"{target} is not a Git repository with an origin")

        actual_slug = repo_slug_from_remote(origin.stdout.strip())
        if actual_slug is None or actual_slug.casefold() != repository.full_name.casefold():
            shown = origin.stdout.strip() or "<missing>"
            return SyncResult(
                repository.full_name,
                "blocked",
                f"{target} already belongs to a different origin: {shown}",
            )

        if fetch:
            if dry_run:
                return SyncResult(repository.full_name, "would-fetch", str(target))
            fetched = run_command(["git", "-C", str(target), "fetch", "--prune", "origin"])
            if fetched.returncode != 0:
                detail = fetched.stderr.strip() or fetched.stdout.strip() or "git fetch failed"
                return SyncResult(repository.full_name, "error", detail)
            return SyncResult(repository.full_name, "fetched", str(target))

        return SyncResult(repository.full_name, "present", str(target))

    if dry_run:
        return SyncResult(repository.full_name, "would-clone", str(target))

    root.mkdir(parents=True, exist_ok=True)
    cloned = run_command(["git", "clone", repository.clone_url_for(protocol), str(target)])
    if cloned.returncode != 0:
        detail = cloned.stderr.strip() or cloned.stdout.strip() or "git clone failed"
        return SyncResult(repository.full_name, "error", detail)

    return SyncResult(repository.full_name, "cloned", str(target))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clone all repositories owned by a GitHub user into sibling workspace folders."
    )
    parser.add_argument("--owner", help="GitHub owner to sync; inferred from this repository when omitted")
    parser.add_argument(
        "--root",
        type=Path,
        help="Workspace root; defaults to the parent directory of the scripts repository",
    )
    parser.add_argument(
        "--protocol",
        choices=("https", "ssh"),
        default="https",
        help="Clone protocol for missing repositories (default: https)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch and prune origin for repositories that are already present; never pulls or resets",
    )
    parser.add_argument(
        "--skip-archived",
        action="store_true",
        help="Do not clone archived repositories",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print intended work without changing the workspace")
    return parser


def print_result(result: SyncResult) -> None:
    print(f"[{result.status:>11}] {result.repository}: {result.detail}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_dir = Path(__file__).resolve().parent
    root = (args.root or default_workspace_root(Path(__file__))).expanduser().resolve()

    if shutil.which("git") is None:
        print("error: git is required but was not found on PATH", file=sys.stderr)
        return 2

    token = resolve_token()
    try:
        owner = resolve_owner(args.owner, token, repo_dir)
        repositories = sorted(iter_repositories(owner, token), key=lambda repo: repo.name.casefold())
    except GitHubError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.skip_archived:
        repositories = [repo for repo in repositories if not repo.archived]

    auth_mode = "authenticated" if token else "public-only"
    print(f"GitHub owner: {owner}")
    print(f"Workspace:    {root}")
    print(f"Discovery:    {auth_mode}")
    print(f"Repositories: {len(repositories)}")

    results = [
        sync_repository(
            repository,
            root,
            protocol=args.protocol,
            fetch=args.fetch,
            dry_run=args.dry_run,
        )
        for repository in repositories
    ]

    for result in results:
        print_result(result)

    failures = [result for result in results if result.status in {"blocked", "error"}]
    print(
        "Summary: "
        + ", ".join(
            f"{status}={sum(result.status == status for result in results)}"
            for status in sorted({result.status for result in results})
        )
    )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
