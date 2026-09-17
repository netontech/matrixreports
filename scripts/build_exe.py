#!/usr/bin/env python3
"""Compile the application to a standalone native binary with Nuitka.

Why compiled: the customer's IT can read Python source, and a Docker image
would not change that - an image is a tar file with the sources sitting inside
it. Nuitka produces machine code, which is the only part of this that actually
protects the work.

Why --standalone and not --onefile: onefile unpacks itself into a temp
directory on every start, which is slower and is exactly the behaviour
anti-virus heuristics dislike. A plain folder starts faster and looks like
what it is.

    python scripts/build_exe.py            # build
    python scripts/build_exe.py --check    # build, then smoke-test it

Run it on the platform you are shipping to - Nuitka does not cross-compile.
For the Windows build you need Windows, plus a C compiler (Nuitka offers to
download MinGW64 on first run).
"""

from __future__ import annotations

import argparse
import pathlib
import platform
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
NAME = "matrixreports"

# Imported dynamically, so Nuitka cannot see them by following imports.
HIDDEN = [
    "pyodbc",            # loaded via the driver name in the config
    "waitress",          # the WSGI server on Windows
    "waitress.server",
    "openpyxl",          # only touched when a workbook is written
]


def _importable(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def build(check: bool) -> int:
    if DIST.exists():
        shutil.rmtree(DIST)

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        f"--output-dir={DIST}",
        f"--output-filename={NAME}",
        # Flask finds templates and static files on disk at runtime, so they
        # have to be carried into the build explicitly.
        "--include-package=webapp",
        "--include-package-data=webapp",
        "--include-package=matrixreports",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=nuitka",
        "--company-name=Neton Technologies",
        "--product-name=Matrix Attendance Reports",
        "--file-description=Attendance reporting for Matrix COSEC",
    ]
    present, missing = [], []
    for module in HIDDEN:
        (present if _importable(module) else missing).append(module)
    cmd += [f"--include-module={module}" for module in present]
    if missing:
        # Nuitka fails hard on a module it cannot find, so skip them - but say
        # so, because a build without pyodbc cannot talk to SQL Server and
        # that must not be discovered on the customer's machine.
        print("WARNING: not installed here, so NOT compiled in: " + ", ".join(missing))
        print("         install them before building for delivery:")
        print("         pip install -e \".[web,sqlserver]\"")
    if platform.system() == "Windows":
        # Keep a console: this runs as a scheduled task and its output is the
        # only diagnostic when something fails at startup.
        cmd.append("--windows-console-mode=force")
    cmd.append(str(ROOT / "packaging" / "launcher.py"))

    print("building:", " ".join(cmd[:6]), "...")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    binary = next(DIST.glob(f"launcher.dist/{NAME}*"), None)
    if binary is None:
        print("build finished but no binary found", file=sys.stderr)
        return 1
    print(f"\nbuilt: {binary}")
    print(f"folder: {binary.parent}  ({_size(binary.parent):,} bytes)")

    return smoke_test(binary) if check else 0


def _size(folder: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def smoke_test(binary: pathlib.Path) -> int:
    """Prove the thing actually runs, and that the templates came with it."""
    print("\nsmoke test:")
    checks = [
        ([str(binary), "--help"], b"discover"),
        ([str(binary), "web", "--help"], b"--hash-password"),
    ]
    for cmd, expected in checks:
        out = subprocess.run(cmd, capture_output=True, timeout=120)
        blob = out.stdout + out.stderr
        ok = expected in blob
        print(f"  {' '.join(cmd[1:]) or '(no args)':<22} {'ok' if ok else 'FAILED'}")
        if not ok:
            print(blob.decode(errors="replace")[:600], file=sys.stderr)
            return 1

    template = binary.parent / "webapp" / "templates" / "report.html"
    print(f"  {'templates bundled':<22} {'ok' if template.exists() else 'FAILED'}")
    return 0 if template.exists() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="smoke-test the build")
    raise SystemExit(build(parser.parse_args().check))
