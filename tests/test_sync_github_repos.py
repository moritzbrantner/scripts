from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sync_github_repos import (
    Repository,
    default_workspace_root,
    repo_slug_from_remote,
    sync_repository,
)


class RemoteParsingTests(unittest.TestCase):
    def test_parses_supported_github_remote_forms(self) -> None:
        remotes = (
            "https://github.com/moritzbrantner/scripts.git",
            "http://github.com/moritzbrantner/scripts.git",
            "git@github.com:moritzbrantner/scripts.git",
            "ssh://git@github.com/moritzbrantner/scripts.git",
            "git://github.com/moritzbrantner/scripts.git",
        )

        for remote in remotes:
            with self.subTest(remote=remote):
                self.assertEqual(repo_slug_from_remote(remote), "moritzbrantner/scripts")

    def test_rejects_non_github_remote(self) -> None:
        self.assertIsNone(repo_slug_from_remote("https://example.com/moritzbrantner/scripts.git"))


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Repository(
            name="demo",
            full_name="moritzbrantner/demo",
            clone_url="https://github.com/moritzbrantner/demo.git",
            ssh_url="git@github.com:moritzbrantner/demo.git",
            archived=False,
        )

    def test_default_workspace_is_parent_of_scripts_repository(self) -> None:
        script = Path("/workspace/scripts/sync_github_repos.py")
        self.assertEqual(default_workspace_root(script), Path("/workspace"))

    def test_dry_run_does_not_create_missing_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = sync_repository(
                self.repository,
                root,
                protocol="https",
                fetch=False,
                dry_run=True,
            )

            self.assertEqual(result.status, "would-clone")
            self.assertFalse((root / "demo").exists())

    def test_existing_matching_repository_is_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "demo"
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "remote",
                    "add",
                    "origin",
                    "git@github.com:moritzbrantner/demo.git",
                ],
                check=True,
            )

            result = sync_repository(
                self.repository,
                Path(temporary_directory),
                protocol="https",
                fetch=False,
                dry_run=False,
            )

            self.assertEqual(result.status, "present")

    def test_existing_non_repository_directory_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "demo"
            target.mkdir()
            (target / "keep-me.txt").write_text("do not overwrite", encoding="utf-8")

            result = sync_repository(
                self.repository,
                Path(temporary_directory),
                protocol="https",
                fetch=False,
                dry_run=False,
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual((target / "keep-me.txt").read_text(encoding="utf-8"), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
