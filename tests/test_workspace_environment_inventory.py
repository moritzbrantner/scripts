from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from workspace_environment_inventory import (
    collect_local_toolchains,
    discover_repositories,
    installed_rust_versions,
    inventory_repository,
    render_json,
)


class InventoryTests(unittest.TestCase):
    def test_collects_native_pins_hold_and_duplicate_execution_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "demo"
            (repo / ".git").mkdir(parents=True)
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / "frontend").mkdir()
            (repo / "package.json").write_text(
                json.dumps({"packageManager": "bun@1.4.2"}),
                encoding="utf-8",
            )
            (repo / "frontend" / "package.json").write_text(
                json.dumps({"packageManager": "bun@1.4.2"}),
                encoding="utf-8",
            )
            (repo / "rust-toolchain.toml").write_text(
                '[toolchain]\nchannel = "1.98.1"\n',
                encoding="utf-8",
            )
            (repo / "global.json").write_text(
                json.dumps({"sdk": {"version": "10.0.100"}}),
                encoding="utf-8",
            )
            (repo / ".repository-environment.toml").write_text(
                "[compatibility_holds.bun]\n"
                'candidate = "1.4.3"\n'
                'tested_revision = "0123456789abcdef0123456789abcdef01234567"\n'
                'reason = "full gate failed"\n',
                encoding="utf-8",
            )
            (repo / ".github" / "workflows" / "ci.yml").write_text(
                "steps:\n  - run: rustup toolchain install 1.98.1\n  - run: bun --version # 1.4.2\n",
                encoding="utf-8",
            )

            inventory = inventory_repository(repo)

            self.assertEqual(
                [(pin.tool, pin.version) for pin in inventory.pins],
                [("bun", "1.4.2"), ("dotnet", "10.0.100"), ("rust", "1.98.1")],
            )
            self.assertEqual(len(inventory.holds), 1)
            self.assertEqual(inventory.holds[0].candidate, "1.4.3")
            cleanup = [reference for reference in inventory.references if reference.kind != "documentation-reference"]
            self.assertEqual(
                {(reference.kind, reference.tool, reference.version) for reference in cleanup},
                {
                    ("execution-duplicate", "bun", "1.4.2"),
                    ("execution-duplicate", "rust", "1.98.1"),
                    ("secondary-native-declaration", "bun", "1.4.2"),
                },
            )
            self.assertEqual(inventory.errors, [])

    def test_documentation_version_is_not_classified_as_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "demo"
            (repo / ".git").mkdir(parents=True)
            (repo / "package.json").write_text(
                json.dumps({"packageManager": "bun@1.4.2"}),
                encoding="utf-8",
            )
            (repo / "README.md").write_text("Use Bun 1.4.2.\n", encoding="utf-8")

            inventory = inventory_repository(repo)

            self.assertEqual(len(inventory.references), 1)
            self.assertEqual(inventory.references[0].kind, "documentation-reference")

    def test_non_exact_native_pin_is_preserved_for_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "demo"
            (repo / ".git").mkdir(parents=True)
            (repo / "rust-toolchain.toml").write_text(
                '[toolchain]\nchannel = "stable"\n',
                encoding="utf-8",
            )

            inventory = inventory_repository(repo)

            self.assertEqual(inventory.pins[0].version, "stable")
            self.assertFalse(inventory.pins[0].exact)

    def test_json_keeps_documentation_references_for_machine_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repo = root / "demo"
            (repo / ".git").mkdir(parents=True)
            (repo / "rust-toolchain.toml").write_text(
                '[toolchain]\nchannel = "1.98.1"\n',
                encoding="utf-8",
            )
            (repo / "README.md").write_text("Rust 1.98.1\n", encoding="utf-8")

            inventory = inventory_repository(repo)
            payload = json.loads(render_json(root, [inventory], None))

            self.assertEqual(payload["pin_groups"]["rust"]["1.98.1"], ["demo"])
            self.assertEqual(payload["repositories"][0]["references"][0]["kind"], "documentation-reference")


class WorkspaceDiscoveryTests(unittest.TestCase):
    def test_discovers_only_sibling_git_repositories_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for name in ("zeta", "Alpha"):
                (root / name / ".git").mkdir(parents=True)
            (root / "not-a-repo").mkdir()
            (root / ".hidden" / ".git").mkdir(parents=True)

            repositories = discover_repositories(root)

            self.assertEqual([repo.name for repo in repositories], ["Alpha", "zeta"])


class LocalToolchainTests(unittest.TestCase):
    def test_parses_only_exact_rustup_toolchains(self) -> None:
        output = """\
1.97.1-x86_64-unknown-linux-gnu
1.98.1-x86_64-unknown-linux-gnu (active, default)
stable-x86_64-unknown-linux-gnu
nightly-2026-09-01-x86_64-unknown-linux-gnu
"""
        self.assertEqual(installed_rust_versions(output), ("1.97.1", "1.98.1"))


if __name__ == "__main__":
    unittest.main()
