"""Verify a committed bundle from a fresh extracted location, without publishing."""

import hashlib
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
        archive = artifacts / manifest["archive"]
        with archive.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != manifest["sha256"]:
            raise SystemExit(
                f"Archive SHA-256 {checksum} does not match manifest {manifest['sha256']}"
            )
        source = scratch / "source"
        source.mkdir()
        # This is our own git archive of the explicitly selected local commit,
        # not an archive downloaded from a registry or supplied by a caller.
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(source)], check=True)
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
