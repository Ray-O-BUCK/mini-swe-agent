"""Create a sanitized workspace from a task's base commit."""
from __future__ import annotations

import argparse
from pathlib import Path

from sweagent.benchmark.spec import load_task
from sweagent.workspace import prepare_workspace_from_commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", help="Task directory or task.yaml")
    parser.add_argument("--out", required=True, help="Destination workspace")
    args = parser.parse_args()

    task = load_task(Path(args.task))
    out = Path(args.out).resolve()
    prepare_workspace_from_commit(task.repo, task.base_commit, out)
    print(f"workspace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
