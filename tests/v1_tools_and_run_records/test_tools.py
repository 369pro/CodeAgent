from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from codeagent.tools import build_default_registry


class ToolsTest(unittest.TestCase):
    def test_default_registry_contains_core_tools(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            registry = build_default_registry(workspace)

            self.assertEqual(
                registry.names(),
                ["bash", "edit_file", "git_diff", "git_status", "glob", "grep", "read_file", "write_file"],
            )

    def test_read_write_edit_enforce_read_before_modify(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            target = root / "note.txt"
            target.write_text("hello world\n", encoding="utf-8")
            registry = build_default_registry(root)

            blocked_write = registry.run("write_file", {"path": "note.txt", "content": "changed\n"})
            self.assertTrue(blocked_write.is_error)
            self.assertIn("read_file must be called", blocked_write.output)

            read = registry.run("read_file", {"path": "note.txt"})
            self.assertFalse(read.is_error)
            self.assertIn("1\thello world", read.output)

            edited = registry.run("edit_file", {"path": "note.txt", "old_string": "world", "new_string": "agent"})
            self.assertFalse(edited.is_error)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello agent\n")

            duplicate = root / "dup.txt"
            duplicate.write_text("x x\n", encoding="utf-8")
            registry.run("read_file", {"path": "dup.txt"})
            failed = registry.run("edit_file", {"path": "dup.txt", "old_string": "x", "new_string": "y"})
            self.assertTrue(failed.is_error)
            self.assertIn("must be unique", failed.output)

    def test_write_file_can_create_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            registry = build_default_registry(root)

            result = registry.run("write_file", {"path": "new/file.txt", "content": "created"})

            self.assertFalse(result.is_error)
            self.assertEqual((root / "new" / "file.txt").read_text(encoding="utf-8"), "created")

    def test_glob_and_grep_skip_common_dirs_and_report_regex_errors(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("needle\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "cache.py").write_text("needle\n", encoding="utf-8")
            registry = build_default_registry(root)

            globbed = registry.run("glob", {"pattern": "**/*.py"})
            self.assertFalse(globbed.is_error)
            self.assertIn("src/app.py", globbed.output)
            self.assertNotIn("__pycache__", globbed.output)

            grepped = registry.run("grep", {"pattern": "needle", "path": ".", "include": "*.py"})
            self.assertFalse(grepped.is_error)
            self.assertIn("src/app.py:1:needle", grepped.output)
            self.assertNotIn("__pycache__", grepped.output)

            invalid = registry.run("grep", {"pattern": "["})
            self.assertTrue(invalid.is_error)
            self.assertIn("invalid regex", invalid.output)

    def test_bash_reports_stdout_stderr_nonzero_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            registry = build_default_registry(workspace)

            output = registry.run(
                "bash",
                {"command": "python3 -c \"import sys; print('out'); print('err', file=sys.stderr); sys.exit(2)\""},
            )
            self.assertTrue(output.is_error)
            self.assertIn("STDOUT", output.output)
            self.assertIn("STDERR", output.output)
            self.assertEqual(output.metadata["returncode"], 2)

            timeout = registry.run("bash", {"command": "python3 -c \"import time; time.sleep(2)\"", "timeout": 1})
            self.assertTrue(timeout.is_error)
            self.assertIn("timed out", timeout.output)

    def test_git_status_and_diff_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            (root / "tracked.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True, capture_output=True)
            (root / "tracked.txt").write_text("two\n", encoding="utf-8")
            registry = build_default_registry(root)

            status = registry.run("git_status", {})
            diff = registry.run("git_diff", {"path": "tracked.txt"})

            self.assertFalse(status.is_error)
            self.assertIn("tracked.txt", status.output)
            self.assertFalse(diff.is_error)
            self.assertIn("-one", diff.output)
            self.assertIn("+two", diff.output)
