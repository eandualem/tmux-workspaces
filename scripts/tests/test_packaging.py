"""Isolation, staging and immutable-source contracts for local packaging tools."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.install_bundle import install

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_existing_prefix_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            sentinel = prefix / "keep"
            sentinel.write_text("owned by caller")
            with self.assertRaises(FileExistsError):
                install(ROOT, prefix, Path(sys.executable), Path("/unused/tmux"))
            self.assertEqual(sentinel.read_text(), "owned by caller")
            self.assertEqual(list(prefix.iterdir()), [sentinel])

    def test_staged_install_uses_final_paths_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            staged, final = base / "staged", base / "final prefix 'quoted'"
            install(ROOT, staged, Path(sys.executable), Path("/unused/tmux"), final)
            staged.rename(final)
            command = final / "bin/tmux-workspaces"
            env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
            env.update(HOME=str(base), PATH="/usr/bin:/bin")
            result = subprocess.run(
                [str(command), "--help"],
                cwd=base,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("--data-dir", result.stdout)
            self.assertFalse((base / ".local").exists())
            self.assertFalse((final / "libexec/tmux-workspaces/.backbone").exists())
            self.assertEqual(list(final.rglob("__pycache__")), [])
            plugin = (final / "share/tmux-workspaces/tmux-workspaces.tmux").read_text()
            self.assertNotIn(str(staged), plugin)

    def test_selected_tmux_wins_over_interpreter_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            python = base / "python-tools/python3"
            selected = base / "tmux-tools/tmux"
            python.parent.mkdir()
            selected.parent.mkdir()
            # A tiny interpreter stand-in observes the installed wrapper's PATH.
            python.write_text("#!/bin/sh\nexec tmux\n")
            python.chmod(0o755)
            for tool, value in ((python.parent / "tmux", "wrong"), (selected, "selected")):
                tool.write_text(f'#!/bin/sh\nprintf "{value}\\n"\n')
                tool.chmod(0o755)
            prefix = base / "installed"
            install(ROOT, prefix, python, selected)
            value = subprocess.check_output([str(prefix / "bin/tmux-workspaces")], text=True)
            self.assertEqual(value, "selected\n")

    def test_invalid_tool_name_and_recursive_prefix_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            nested = source / "tmux_workspaces/nested"
            with self.assertRaisesRegex(ValueError, "outside"):
                install(source, nested, Path(sys.executable), Path("/unused/tmux"))
            self.assertFalse(nested.exists())
            with self.assertRaisesRegex(ValueError, "filename"):
                install(source, source / "prefix", Path(sys.executable), Path("/unused/custom"))
            self.assertFalse((source / "prefix").exists())

    def test_archive_and_formula_come_from_commit_not_working_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            (root / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "scripts/package_source.py", root / "scripts/package_source.py")
            template = root / "packaging/homebrew/tmux-workspaces.rb.in"
            template.parent.mkdir(parents=True)
            template.write_text("url @SOURCE_URL@\nsha256 @SOURCE_SHA256@\n")
            (root / ".gitignore").write_text(".backbone/\n")
            (root / "payload").write_text("committed")

            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "-q")
            git("add", ".")
            git(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "fixture",
            )
            commit = git("rev-parse", "HEAD")
            nested_script = root / "extracted/scripts/package_source.py"
            nested_script.parent.mkdir(parents=True)
            shutil.copy2(root / "scripts/package_source.py", nested_script)
            refused_output = Path(directory) / "refused"
            result = subprocess.run(
                [sys.executable, str(nested_script), "--output", str(refused_output)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("own Git checkout", result.stderr)
            self.assertFalse(refused_output.exists())
            (root / "payload").write_text("uncommitted")
            template.write_text("uncommitted formula")
            (root / ".backbone").mkdir()
            (root / ".backbone/private").write_text("must stay local")
            checksums = []
            for index in range(2):
                output = Path(directory) / f"artifacts{index}"
                subprocess.run(
                    [
                        sys.executable,
                        str(root / "scripts/package_source.py"),
                        "--output",
                        str(output),
                    ],
                    check=True,
                    capture_output=True,
                )
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["commit"], commit)
                payload = (output / manifest["archive"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["sha256"])
                checksums.append(manifest["sha256"])
                with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                    self.assertFalse(any(".backbone" in name for name in archive.getnames()))
                    self.assertEqual(
                        archive.extractfile(f"tmux-workspaces-{commit}/payload").read(),
                        b"committed",
                    )
                formula = (output / "tmux-workspaces.rb").read_text()
                self.assertIn(manifest["sha256"], formula)
                self.assertNotIn("uncommitted", formula)
            self.assertEqual(checksums[0], checksums[1])


if __name__ == "__main__":
    unittest.main()
