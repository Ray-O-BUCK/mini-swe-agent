"""Rebuild examples/toy-bench with deterministic base/fix commits.

The benchmark task tasks/smoke/toy-001 pins two commit SHAs. Git commits
include timestamps and exact file bytes, so this script recreates them:

- fixed author/committer name, email and dates;
- base files copied byte-for-byte from examples/toy-bench-src/;
- the fix writes calc.py with CRLF and no trailing newline.

Usage:
    python scripts/setup_toy_bench.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "examples" / "toy-bench-src"
DST = ROOT / "examples" / "toy-bench"

BASE_DATE = "1794545522 +0800"
FIX_DATE = "1794545647 +0800"
BASE_COMMIT = "b1664b6859a8ace37c6045c3bc8ab11794eea837"
FIX_CALC = b"def add(a: int, b: int) -> int:\r\n    return a + b"


def git(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=DST,
        check=True,
        capture_output=True,
        env=env,
    )


def main() -> int:
    if not SRC.is_dir():
        print(f"missing source directory: {SRC}", file=sys.stderr)
        return 1
    if DST.exists():
        print(
            f"{DST} already exists; remove it first if you want to rebuild",
            file=sys.stderr,
        )
        return 1

    shutil.copytree(SRC, DST)
    git("init", "-q")
    git("config", "user.name", "Bench")
    git("config", "user.email", "bench@example.com")
    git("config", "core.autocrlf", "false")
    git("add", "-A")

    base_env = os.environ.copy()
    base_env["GIT_AUTHOR_DATE"] = BASE_DATE
    base_env["GIT_COMMITTER_DATE"] = BASE_DATE
    git("commit", "-q", "-m", "base", env=base_env)

    (DST / "calc.py").write_bytes(FIX_CALC)
    git("add", "-A")

    fix_env = os.environ.copy()
    fix_env["GIT_AUTHOR_DATE"] = FIX_DATE
    fix_env["GIT_COMMITTER_DATE"] = FIX_DATE
    git("commit", "-q", "-m", "fix", env=fix_env)

    git("checkout", "-q", BASE_COMMIT)
    print(f"rebuilt {DST}")
    print(f"base={BASE_COMMIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
