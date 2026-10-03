#!/usr/bin/env python3
"""Inventory authoritative toolchain pins and cleanup candidates across a workspace.

The script is intentionally read-only. It treats repository-native version files as
sources of truth, reports environment-v1 compatibility holds, and finds exact
version literals repeated in execution/setup files where they can drift away from
the canonical pin.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Iterable


SKIP_DIRECTORIES = {
    ".git",
    ".next",
    ".turbo",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
}

EXECUTION_SUFFIXES = {".json", ".mjs", ".py", ".sh", ".ts", ".yml", ".yaml"}
VERSION_RE = re.compile(r"^v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
RUSTUP_EXACT_RE = re.compile(r"^(\d+\.\d+\.\d+)(?:-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)?")


@dataclass(frozen=True)
class Pin:
    tool: str
    version: str
    path: str
    exact: bool


@dataclass(frozen=True)
class CompatibilityHold:
    tool: str
    candidate: str
    tested_revision: str
    reason: str
    path: str = ".repository-environment.toml"


@dataclass(frozen=True)
class Reference:
    tool: str
    version: str
    path: str
    line: int
    kind: str


@dataclass
class RepositoryInventory:
    name: str
    path: str
    pins: list[Pin]
    holds: list[CompatibilityHold]
    references: list[Reference]
    errors: list[str]


@dataclass(frozen=True)
class LocalToolchains:
    rust_installed: tuple[str, ...]
    rust_unreferenced: tuple[str, ...]
    bun_version: str | None


def default_workspace_root(script_path: Path) -> Path:
    return script_path.resolve().parent.parent


def discover_repositories(root: Path) -> list[Path]:
    repositories: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / ".git").exists():
            repositories.append(child)
    return repositories


def is_exact_version(value: str) -> bool:
    return bool(VERSION_RE.fullmatch(value.strip()))


def normalized_version(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("v") and len(stripped) > 1 and stripped[1].isdigit():
        return stripped[1:]
    return stripped


def read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError("expected a TOML table")
    return data


def collect_pins(repo: Path, errors: list[str]) -> list[Pin]:
    pins: list[Pin] = []

    package_path = repo / "package.json"
    if package_path.is_file():
        try:
            package = read_json(package_path)
            package_manager = package.get("packageManager")
            if isinstance(package_manager, str) and package_manager.startswith("bun@"):
                version = normalized_version(package_manager.removeprefix("bun@"))
                pins.append(Pin("bun", version, "package.json#packageManager", is_exact_version(version)))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"package.json: {error}")

    rust_path = repo / "rust-toolchain.toml"
    if rust_path.is_file():
        try:
            rust = read_toml(rust_path)
            toolchain = rust.get("toolchain")
            if isinstance(toolchain, dict):
                channel = toolchain.get("channel")
                if isinstance(channel, str):
                    version = normalized_version(channel)
                    pins.append(Pin("rust", version, "rust-toolchain.toml#toolchain.channel", is_exact_version(version)))
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            errors.append(f"rust-toolchain.toml: {error}")

    dotnet_path = repo / "global.json"
    if dotnet_path.is_file():
        try:
            global_json = read_json(dotnet_path)
            sdk = global_json.get("sdk")
            if isinstance(sdk, dict):
                version_value = sdk.get("version")
                if isinstance(version_value, str):
                    version = normalized_version(version_value)
                    pins.append(Pin("dotnet", version, "global.json#sdk.version", is_exact_version(version)))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"global.json: {error}")

    for filename, tool in ((".node-version", "node"), (".nvmrc", "node"), (".python-version", "python")):
        path = repo / filename
        if not path.is_file():
            continue
        try:
            value = normalized_version(path.read_text(encoding="utf-8").strip())
            if value:
                pins.append(Pin(tool, value, filename, is_exact_version(value)))
        except OSError as error:
            errors.append(f"{filename}: {error}")

    return sorted(pins, key=lambda pin: (pin.tool, pin.path, pin.version))


def collect_holds(repo: Path, errors: list[str]) -> list[CompatibilityHold]:
    path = repo / ".repository-environment.toml"
    if not path.is_file():
        return []

    try:
        config = read_toml(path)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        errors.append(f".repository-environment.toml: {error}")
        return []

    holds = config.get("compatibility_holds")
    if not isinstance(holds, dict):
        return []

    result: list[CompatibilityHold] = []
    for tool, raw_hold in sorted(holds.items()):
        if not isinstance(tool, str) or not isinstance(raw_hold, dict):
            continue
        candidate = raw_hold.get("candidate")
        tested_revision = raw_hold.get("tested_revision")
        reason = raw_hold.get("reason")
        if all(isinstance(value, str) for value in (candidate, tested_revision, reason)):
            result.append(
                CompatibilityHold(
                    tool=tool,
                    candidate=candidate,
                    tested_revision=tested_revision,
                    reason=reason,
                )
            )
    return result


def iter_files(root: Path) -> Iterable[Path]:
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower(), reverse=True)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRECTORIES:
                    stack.append(entry)
            elif entry.is_file():
                yield entry


def execution_files(repo: Path) -> list[Path]:
    candidates: set[Path] = set()
    for relative in (Path(".github/workflows"), Path(".github/actions"), Path("scripts"), Path(".devcontainer")):
        base = repo / relative
        if base.is_dir():
            for path in iter_files(base):
                if path.suffix.lower() in EXECUTION_SUFFIXES or path.name.startswith("Dockerfile"):
                    candidates.add(path)

    for name in ("Dockerfile", "Makefile", "justfile", "Taskfile.yml", "Taskfile.yaml"):
        path = repo / name
        if path.is_file():
            candidates.add(path)

    return sorted(candidates, key=lambda path: path.relative_to(repo).as_posix())


def documentation_files(repo: Path) -> list[Path]:
    candidates: set[Path] = set()
    for pattern in ("README*", "CONTRIBUTING*"):
        for path in repo.glob(pattern):
            if path.is_file():
                candidates.add(path)
    docs = repo / "docs"
    if docs.is_dir():
        for path in iter_files(docs):
            if path.suffix.lower() in {".md", ".mdx", ".txt"}:
                candidates.add(path)
    return sorted(candidates, key=lambda path: path.relative_to(repo).as_posix())


def matching_lines(path: Path, versions: dict[str, str]) -> list[tuple[str, str, int]]:
    try:
        if path.stat().st_size > 1_000_000:
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    matches: list[tuple[str, str, int]] = []
    for line_number, line in enumerate(lines, start=1):
        for tool, version in versions.items():
            if version and version in line:
                matches.append((tool, version, line_number))
    return matches


def collect_secondary_package_manager_refs(repo: Path, bun_version: str | None) -> list[Reference]:
    if not bun_version:
        return []

    references: list[Reference] = []
    for path in iter_files(repo):
        if path.name != "package.json" or path == repo / "package.json":
            continue
        try:
            package = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        package_manager = package.get("packageManager")
        if package_manager == f"bun@{bun_version}":
            references.append(
                Reference(
                    tool="bun",
                    version=bun_version,
                    path=path.relative_to(repo).as_posix() + "#packageManager",
                    line=0,
                    kind="secondary-native-declaration",
                )
            )
    return references


def collect_references(repo: Path, pins: list[Pin]) -> list[Reference]:
    versions: dict[str, str] = {}
    for pin in pins:
        versions.setdefault(pin.tool, pin.version)

    references: list[Reference] = []
    for kind, paths in (("execution-duplicate", execution_files(repo)), ("documentation-reference", documentation_files(repo))):
        for path in paths:
            for tool, version, line_number in matching_lines(path, versions):
                references.append(
                    Reference(
                        tool=tool,
                        version=version,
                        path=path.relative_to(repo).as_posix(),
                        line=line_number,
                        kind=kind,
                    )
                )

    references.extend(collect_secondary_package_manager_refs(repo, versions.get("bun")))
    return sorted(references, key=lambda ref: (ref.kind, ref.tool, ref.path, ref.line))


def inventory_repository(repo: Path) -> RepositoryInventory:
    errors: list[str] = []
    pins = collect_pins(repo, errors)
    return RepositoryInventory(
        name=repo.name,
        path=str(repo),
        pins=pins,
        holds=collect_holds(repo, errors),
        references=collect_references(repo, pins),
        errors=errors,
    )


def installed_rust_versions(output: str) -> tuple[str, ...]:
    versions: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        match = RUSTUP_EXACT_RE.match(line)
        if match:
            versions.add(match.group(1))
    return tuple(sorted(versions))


def run_version_command(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def collect_local_toolchains(inventories: list[RepositoryInventory]) -> LocalToolchains:
    rust_output = run_version_command(["rustup", "toolchain", "list"])
    installed = installed_rust_versions(rust_output or "")
    referenced = {
        pin.version
        for inventory in inventories
        for pin in inventory.pins
        if pin.tool == "rust" and pin.exact
    }
    unreferenced = tuple(version for version in installed if version not in referenced)
    bun_output = run_version_command(["bun", "--version"])
    bun_version = bun_output.splitlines()[0].strip() if bun_output else None
    return LocalToolchains(installed, unreferenced, bun_version)


def pin_groups(inventories: list[RepositoryInventory]) -> dict[str, dict[str, list[str]]]:
    groups: dict[str, dict[str, list[str]]] = {}
    for inventory in inventories:
        for pin in inventory.pins:
            groups.setdefault(pin.tool, {}).setdefault(pin.version, []).append(inventory.name)
    for versions in groups.values():
        for repositories in versions.values():
            repositories.sort()
    return dict(sorted((tool, dict(sorted(versions.items()))) for tool, versions in groups.items()))


def duplicate_execution_references(inventories: list[RepositoryInventory]) -> list[tuple[str, Reference]]:
    return [
        (inventory.name, reference)
        for inventory in inventories
        for reference in inventory.references
        if reference.kind in {"execution-duplicate", "secondary-native-declaration"}
    ]


def render_human(
    root: Path,
    inventories: list[RepositoryInventory],
    local: LocalToolchains | None,
) -> str:
    lines = [
        f"Workspace environment inventory: {root}",
        f"Repositories scanned: {len(inventories)}",
        "",
        "Authoritative/native pins:",
    ]

    groups = pin_groups(inventories)
    if not groups:
        lines.append("  (none)")
    for tool, versions in groups.items():
        lines.append(f"  {tool}:")
        for version, repositories in versions.items():
            lines.append(f"    {version}: {', '.join(repositories)}")

    non_exact = [
        (inventory.name, pin)
        for inventory in inventories
        for pin in inventory.pins
        if not pin.exact
    ]
    if non_exact:
        lines.extend(["", "Non-exact native pins:"])
        for repository, pin in non_exact:
            lines.append(f"  {repository}: {pin.tool}={pin.version} ({pin.path})")

    holds = [(inventory.name, hold) for inventory in inventories for hold in inventory.holds]
    lines.extend(["", "Compatibility holds:"])
    if not holds:
        lines.append("  (none)")
    else:
        for repository, hold in holds:
            lines.append(f"  {repository}: {hold.tool} candidate {hold.candidate} @ {hold.tested_revision[:12]} — {hold.reason}")

    duplicates = duplicate_execution_references(inventories)
    lines.extend(["", "Potential cleanup — duplicated execution/native pins:"])
    if not duplicates:
        lines.append("  (none)")
    else:
        for repository, reference in duplicates:
            location = reference.path if reference.line == 0 else f"{reference.path}:{reference.line}"
            lines.append(f"  {repository}: {reference.tool} {reference.version} repeated in {location} [{reference.kind}]")

    errors = [(inventory.name, error) for inventory in inventories for error in inventory.errors]
    if errors:
        lines.extend(["", "Read/parse errors:"])
        for repository, error in errors:
            lines.append(f"  {repository}: {error}")

    if local is not None:
        lines.extend(["", "Local installations:"])
        lines.append(f"  Bun: {local.bun_version or '(not found)'}")
        lines.append(f"  Exact Rust toolchains: {', '.join(local.rust_installed) or '(none found)'}")
        lines.append(f"  Unreferenced exact Rust toolchains: {', '.join(local.rust_unreferenced) or '(none)'}")

    lines.extend(
        [
            "",
            "Notes:",
            "  - Documentation references are collected in JSON but are not cleanup candidates by default.",
            "  - This command never changes repositories, toolchains, caches, or installed software.",
        ]
    )
    return "\n".join(lines)


def render_json(
    root: Path,
    inventories: list[RepositoryInventory],
    local: LocalToolchains | None,
) -> str:
    payload: dict[str, object] = {
        "workspace": str(root),
        "repositories": [
            {
                "name": inventory.name,
                "path": inventory.path,
                "pins": [asdict(pin) for pin in inventory.pins],
                "compatibility_holds": [asdict(hold) for hold in inventory.holds],
                "references": [asdict(reference) for reference in inventory.references],
                "errors": inventory.errors,
            }
            for inventory in inventories
        ],
        "pin_groups": pin_groups(inventories),
    }
    if local is not None:
        payload["local_toolchains"] = asdict(local)
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory workspace toolchain pins, compatibility holds, and duplicate version declarations."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_workspace_root(Path(__file__)),
        help="workspace containing sibling Git repositories (default: parent of this scripts repository)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--local",
        action="store_true",
        help="also inspect installed Bun and exact rustup toolchains; still read-only",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"workspace does not exist: {root}", file=sys.stderr)
        return 2

    inventories = [inventory_repository(repo) for repo in discover_repositories(root)]
    local = collect_local_toolchains(inventories) if args.local else None
    output = render_json(root, inventories, local) if args.json else render_human(root, inventories, local)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
