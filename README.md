# scripts

Small, idempotent Python utilities for bootstrapping and maintaining a local development environment.

## Principles

- **Idempotent by default:** rerunning a script should converge on the desired state instead of duplicating work.
- **Non-destructive:** existing local work is not reset, overwritten, or pulled implicitly.
- **Dry-run friendly:** scripts that mutate local state should expose a preview mode.
- **Standard-library first:** Python dependencies are added only when they materially improve the tool.
- **Useful exit codes:** automation should be able to distinguish success from conflicts or failures.

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

By default the script clones through HTTPS. Use SSH when that is how your local GitHub authentication is configured:

```bash
python sync_github_repos.py --protocol ssh
```

Existing matching Git repositories are left untouched. To refresh remote refs without changing a working tree or branch, opt into fetch mode:

```bash
python sync_github_repos.py --fetch
```

Archived repositories are included because the default contract is “all owned repositories.” Use `--skip-archived` to omit them.

### Authentication

The script looks for `GH_TOKEN`, then `GITHUB_TOKEN`, then an existing `gh auth` login. Authenticated discovery includes private repositories the credential can access and owns. Without authentication, GitHub's public repository API is used, so only public repositories can be discovered.

### Safety behavior

For every discovered repository, the script follows this rule:

| Local state | Action |
| --- | --- |
| Directory missing | Clone it |
| Matching Git repository present | Leave it unchanged |
| Matching Git repository present with `--fetch` | `git fetch --prune origin` |
| Non-Git directory present | Report `blocked`; never overwrite |
| Git directory points at another origin | Report `blocked`; never rewrite the remote |

A run exits non-zero if any repository is blocked or a Git operation fails.

## Validation

```bash
python -m compileall sync_github_repos.py tests
python -m unittest discover -s tests -v
```

## Next useful scripts

The repository should stay focused on small local-development operations rather than becoming another orchestration framework. Good next additions are:

1. `workspace_doctor.py` — verify required local tools, versions, authentication, and expected sibling repositories without modifying them.
2. `workspace_run.py` — run one deterministic command across selected sibling repositories and summarize exit codes without hiding failures.
3. `sync_default_branches.py` — safely fetch repositories and fast-forward only clean default branches; never touch dirty or diverged worktrees.
4. `bootstrap_dev_tools.py` — idempotently install or verify the small set of global developer tools you actually standardize on.
