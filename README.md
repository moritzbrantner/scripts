# scripts

Small, idempotent Python utilities for bootstrapping and maintaining local development repositories and workspaces.

## Principles

- **Idempotent by default:** rerunning an operation should converge instead of duplicating work.
- **Non-destructive:** existing source, branches, remotes, and dirty worktrees are preserved unless an explicit mutating operation says otherwise.
- **Dry-run first:** source/worktree/GitHub mutations require `--apply` where applicable.
- **Standard-library first:** the repository itself has no Python package dependency requirement.
- **Fail closed on acceptance:** missing required-check or exact-head evidence is not treated as green.
- **One targeting model:** every repository operation uses the same single-repository/fleet resolver.
- **Thin orchestration:** durable analyzers and engineering policy stay in their semantic owners such as `coding-tooling` and `coding-agent-conventions`.

## Repository operations

`repo-ops` provides 33 deterministic operations. Every operation works in either of two useful modes without changing the command:

```text
~/dev/ecs-lab$ ../scripts/bin/repo-health
# auto scope: ecs-lab only

~/dev$ ./scripts/bin/repo-health
# auto scope: every direct child Git repository in ~/dev
```

Auto scope deliberately scans only direct child repositories. It does not wander recursively into nested fixtures, submodules, generated trees, or vendored repositories.

You can override discovery explicitly:

```bash
./scripts/bin/fleet-status --scope fleet
./scripts/bin/fleet-status --include 'ecs-*' --exclude '*archive*'
./scripts/bin/repo-health --root ~/dev/ecs-lab --scope repo
```

If a workspace parent is itself a Git repository, use `--scope fleet` when you intentionally want its direct child repositories instead of the parent repository.

All commands are also available through the central dispatcher:

```bash
./scripts/bin/repo-ops repo-health
./scripts/bin/repo-ops repo-graph --json
```

### Command catalog

| Command | Purpose |
| --- | --- |
| `repo-health` | Run the repository's deterministic validation plan, preferring `coding-tooling` when available. |
| `pr-accept` | Verify exact PR head, merge state, review state, unresolved threads, and required checks; missing check evidence blocks acceptance. |
| `pr-stack-status` | Show open PR base/head relationships and identify stacked PRs. |
| `fleet-status` | Summarize local branch/worktree/upstream state; `--github` adds PR/latest Actions status. |
| `repo-bootstrap` | Add safe universal foundation files (`.editorconfig` and shared Renovate config) without overwriting existing files. |
| `repo-drift` | Check shared Renovate adoption and optionally compare canonical files against a baseline repository. |
| `workflow-pin` | Find GitHub Actions references that are not pinned to immutable 40-character SHAs. |
| `dependency-sync` | Report shared npm/Cargo dependencies that have drifted to different declared versions across repositories. |
| `duplicate-code-scan` | Find structurally identical function bodies across Python, Rust, TypeScript/JavaScript, and C# source. |
| `extract-candidate` | Restrict duplication evidence to cross-repository candidates and suggest a likely long-term owner. |
| `pages-smoke` | Run a discovered repository-declared Pages/web build or static surface smoke check. |
| `pages-inventory` | Inventory Pages workflow evidence and the local build command used for smoke validation. |
| `dogfood-check` | Prefer the public `coding-tooling` Pages analyzer in a headless browser, then fall back to local `coding-tooling`; save exact-head evidence. |
| `changed-only` | Map changed paths to the smallest relevant capability set and include `coding-tooling affected` evidence when available. |
| `ci-reproduce` | Inspect a failed Actions run and pair failed job steps with the local full validation plan. |
| `toolchain-fingerprint` | Record Git/Python/GitHub CLI/Bun/Node/Rust/.NET versions plus exact lockfile hashes. |
| `rust-workspace-audit` | Run format, Clippy, workspace tests, and package checks for Rust workspaces. |
| `ts-workspace-audit` | Run repository-declared format/lint/typecheck/test/Storybook/build scripts when present. |
| `expo-readiness` | Run Expo Doctor plus repository-declared TypeScript/test/web-build readiness checks. |
| `dotnet-api-audit` | Run locked restore, warning-as-error build, and tests for detected .NET solutions/projects. |
| `roadmap-next` | Deterministically rank open issues by explicit priority/ready labels and issue number. |
| `stale-work` | Report open PRs/issues older than the configured inactivity threshold. |
| `issue-from-finding` | Convert deterministic finding JSON into de-duplicated GitHub issues; dry-run unless `--apply`. |
| `release-evidence` | Write exact-head toolchain, lockfile, validation-plan, and optional executed-validation evidence. |
| `repo-graph` | Build repository dependency edges from npm sibling dependencies, Cargo path dependencies, and reusable Actions workflows; optionally emit JSON/DOT. |
| `ownership-check` | Flag direct sibling path dependencies that couple repository working trees. |
| `workspace-clean` | Remove only candidate cache/build directories that Git itself confirms are ignored; dry-run unless `--apply`. |
| `batch-pr` | Apply one deterministic command to clean repositories, commit it on a named branch, push, and open one PR per repository; dry-run unless `--apply`. |
| `merge-green` | Merge only an exact-head PR with non-empty green required-check evidence and no review blockers; dry-run unless `--apply`. |
| `workspace-doctor` | Verify the tools each detected repository needs. |
| `workspace-run` | Run one argv-safe command independently in every selected repository and retain per-repo exit status. |
| `sync-default-branches` | Fetch and fast-forward only clean checked-out default branches with no local divergence; dry-run unless `--apply`. |
| `bootstrap-dev-tools` | Verify the standardized tools needed by the selected repositories without silently installing machine-global software. |

Examples:

```bash
# Fleet status including GitHub information
./scripts/bin/fleet-status --github

# Find only cross-repository duplicate-function candidates
./scripts/bin/duplicate-code-scan --cross-repo-only --json
./scripts/bin/extract-candidate --json

# Create a dependency graph
./scripts/bin/repo-graph --output /tmp/repos.json --dot /tmp/repos.dot

# Preview cleanup, then explicitly apply it
./scripts/bin/workspace-clean
./scripts/bin/workspace-clean --apply

# Preview a uniform fleet edit; only --apply creates branches/commits/PRs
./scripts/bin/batch-pr \
  --branch chore/example \
  --title 'chore: example fleet change' \
  --run 'python some_deterministic_edit.py'

# Acceptance never interprets missing required checks as green
./scripts/bin/pr-accept --pr 42 --json
./scripts/bin/merge-green --pr 42
./scripts/bin/merge-green --pr 42 --apply
```

### `coding-tooling` dogfooding

`dogfood-check` keeps `scripts` as orchestration rather than duplicating deterministic analyzers. For a public GitHub repository it first tries the `coding-tooling` GitHub Pages `run.json` surface through an installed headless Chromium-compatible browser. If browser execution is unavailable, it falls back to the local `coding-tooling` CLI/sibling checkout.

Evidence is stamped with the target repository's exact `HEAD` and written under `.artifacts/repo-ops/dogfood/`, which is ignored by Git. The local CLI remains authoritative for operations that need repository execution or mutation.

### Mutation boundaries

`repo-bootstrap`, `issue-from-finding`, `workspace-clean`, `sync-default-branches`, `batch-pr`, and `merge-green` require `--apply` before their corresponding mutation occurs. `batch-pr` refuses dirty repositories. `workspace-clean` refuses any directory that `git check-ignore` does not confirm as ignored. `sync-default-branches` refuses dirty, non-default, ahead, or diverged worktrees. `merge-green` supplies the observed PR head SHA to GitHub's merge API, so a moved head cannot be merged on stale evidence.

`release-evidence` and `dogfood-check` write generated evidence under `.artifacts/`; they do not modify authored source.

## Sync GitHub repositories

`sync_github_repos.py` discovers every repository owned by a GitHub account and makes sure each repository has a sibling directory in the local workspace.

If this repository lives at `~/dev/scripts`, the default workspace is `~/dev`, so a repository named `audio-analysis` is cloned to `~/dev/audio-analysis`.

```bash
python sync_github_repos.py --dry-run
python sync_github_repos.py
```

The owner is inferred from the `origin` remote of this repository. You can override both the owner and workspace explicitly:

```bash
python sync_github_repos.py --owner moritzbrantner --root ~/dev
```

Existing matching Git repositories are left untouched. With `--fetch`, only remote refs are refreshed; worktrees and branches are not implicitly changed. A non-Git directory or mismatching origin is reported as blocked rather than overwritten.

The script looks for `GH_TOKEN`, then `GITHUB_TOKEN`, then an existing `gh auth` login. Without authentication, discovery falls back to GitHub's public repository API.

## Validation

```bash
python -m compileall sync_github_repos.py repo_ops.py repo_ops_base.py repo_ops_checks.py repo_ops_analysis.py repo_ops_remote.py repo_ops_mutation.py tests
python -m unittest discover -s tests -v
bin/repo-ops --help
bin/fleet-status --help
```

CI also dogfoods `workflow-pin` against its own workflow so mutable action references cannot silently creep back in.
