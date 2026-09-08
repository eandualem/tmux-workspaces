"""Create a local immutable source archive and formula; never upload or install."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD", help="committed source revision")
    parser.add_argument("--output", required=True, type=Path, help="new artifact directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    def git(*arguments: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *arguments])

    checkout = Path(git("rev-parse", "--show-toplevel").decode().removesuffix("\n")).resolve()
    if checkout != root:
        raise SystemExit("Run package_source.py from its own Git checkout, not an extracted copy")
    commit = git("rev-parse", "--verify", "--end-of-options", args.revision + "^{commit}")
    commit = commit.decode().strip()
    # Read the formula from the same commit as the source; working edits must
    # never silently change the recipe for a pinned archive.
    template = git("show", f"{commit}:packaging/homebrew/tmux-workspaces.rb.in").decode()
    archive = git("archive", "--format=tar", f"--prefix=tmux-workspaces-{commit}/", commit)
    # gzip.compress(mtime=0) delegates its header to zlib on Python 3.11/3.12.
    # Fix the filename, timestamp, level and OS byte through the streaming writer.
    buffer = io.BytesIO()
    with gzip.GzipFile(
        fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=9
    ) as compressed:
        compressed.write(archive)
    payload = buffer.getvalue()
    checksum = hashlib.sha256(payload).hexdigest()
    output = args.output.expanduser().absolute()
    output.mkdir(parents=True)
    source = output / f"tmux-workspaces-{commit}.tar.gz"
    source.write_bytes(payload)
    values = {
        "SOURCE_URL": source.as_uri(),
        "SOURCE_VERSION": "0.1.0-pre." + commit,
        "SOURCE_SHA256": checksum,
    }
    for key, value in values.items():
        # JSON strings also quote the simple URI/version/hash safely in Ruby;
        # escape Ruby interpolation as file paths may contain # followed by {.
        template = template.replace("@" + key + "@", json.dumps(value).replace("#{", r"\#{"))
    (output / "tmux-workspaces.rb").write_text(template)
    (output / "manifest.json").write_text(
        json.dumps({"commit": commit, "sha256": checksum, "archive": source.name}, indent=2) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
