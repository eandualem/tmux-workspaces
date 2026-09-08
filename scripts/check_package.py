"""Verify a committed bundle from a fresh extracted location, without publishing."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="wsv-package-") as directory:
        scratch = Path(directory)
        artifacts = scratch / "artifacts"
        subprocess.run(
            [sys.executable, str(root / "scripts/package_source.py"), "--output", str(artifacts)],
            check=True,
        )
        manifest = json.loads((artifacts / "manifest.json").read_text())
        source = scratch / "source"
        source.mkdir()
        # This is our own git archive of the explicitly selected local commit,
        # not an archive downloaded from a registry or supplied by a caller.
        subprocess.run(
            ["tar", "-xzf", str(artifacts / manifest["archive"]), "-C", str(source)], check=True
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.integration.smoke_packaging",
                "--source",
                str(source / ("tmux-workspaces-" + manifest["commit"])),
            ],
            cwd=root,
            check=True,
        )
        print(f"Installed source commit {manifest['commit']}; SHA-256 {manifest['sha256']}")


if __name__ == "__main__":
    main()
