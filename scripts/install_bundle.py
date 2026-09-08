"""Stage the source-bundle packaging prototype into a new, explicit prefix.

No package manager, user settings, libraries or existing installation is changed.
The Homebrew prototype calls this same installer.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path


def executable(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"Not an executable: {path}")
    # Python launchers need a portable single-interpreter shebang. Installation
    # prefixes may contain spaces; the interpreter path may not.
    if any(char.isspace() for char in str(path)):
        raise ValueError("Interpreter/tool paths must not contain whitespace")
    return path


def shell_script(path: Path, command: list[str], tool_path: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        f"PATH={shlex.quote(tool_path)}:${{PATH:-/usr/bin:/bin}}\nexport PATH\n"
        f'exec {shlex.join(command)} "$@"\n'
    )
    path.chmod(0o755)


def install(
    source: Path, prefix: Path, python: Path, tmux: Path, runtime_prefix: Path | None = None
) -> None:
    if tmux.name != "tmux":
        raise ValueError("--tmux must name a tmux executable (its filename must be tmux)")
    if prefix.resolve().is_relative_to((source / "tmux_workspaces").resolve()):
        raise ValueError("Installation prefix must be outside the source Python package")
    # mkdir without exist_ok refuses even an empty pre-existing installation.
    # Never merge, overwrite or remove a prefix supplied by the caller.
    prefix.mkdir(parents=True)
    root = prefix / "libexec/tmux-workspaces"
    runtime_root = (runtime_prefix or prefix) / "libexec/tmux-workspaces"
    root.mkdir(parents=True)
    shutil.copytree(
        source / "tmux_workspaces",
        root / "tmux_workspaces",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for relative in (
        "run",
        "ghostty",
        "scripts/tmux_plugin.py",
        "integrations/ghostty.conf",
        "pyproject.toml",
        "LICENSE",
        "README.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
        if relative in {"run", "ghostty", "scripts/tmux_plugin.py"}:
            lines = target.read_text().splitlines(keepends=True)
            target.write_text(f"#!{python}\n" + "".join(lines[1:]))
            target.chmod(0o755)
    # The application invokes literal `tmux`; its explicitly selected directory
    # must win even when the interpreter's directory also contains another tmux.
    tool_path = os.pathsep.join(dict.fromkeys((str(tmux.parent), str(python.parent))))
    # TPM stores this Python script in its binding. The later new-window starts
    # in the outer server's environment, after our shell loader has exited.
    # Reapply tool selection here instead of relying on the loader's environment.
    (root / "scripts/tmux_plugin.py").write_text(
        f"#!{python}\n"
        '"""Installed TPM bootstrap, used both when loading and invoking its binding."""\n'
        "import os\nimport sys\nfrom pathlib import Path\n"
        f"os.environ['PATH'] = {tool_path!r} + os.pathsep + os.environ.get('PATH', os.defpath)\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from tmux_workspaces.tmux_plugin import main\n"
        "raise SystemExit(main())\n"
    )
    for name, script in (("tmux-workspaces", "run"), ("tmux-workspaces-ghostty", "ghostty")):
        shell_script(prefix / "bin" / name, [str(python), str(runtime_root / script)], tool_path)
    shell_script(
        prefix / "share/tmux-workspaces/tmux-workspaces.tmux",
        [str(python), str(runtime_root / "scripts/tmux_plugin.py"), "install"],
        tool_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path, help="new installation directory")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--tmux", default=shutil.which("tmux"))
    parser.add_argument(
        "--runtime-prefix", type=Path, help="final location when staging for a package manager"
    )
    args = parser.parse_args()
    try:
        if not args.tmux:
            raise ValueError("tmux is required; install tmux 3.3+ or supply --tmux")
        python, tmux = executable(args.python), executable(args.tmux)
        install(
            Path(__file__).resolve().parents[1],
            args.prefix.expanduser().absolute(),
            python,
            tmux,
            args.runtime_prefix.expanduser().absolute() if args.runtime_prefix else None,
        )
    except (OSError, ValueError) as error:
        print(f"Bundle installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
