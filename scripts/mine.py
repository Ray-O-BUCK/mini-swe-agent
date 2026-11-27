"""Mine candidate bugfix commits from a local git clone.

Usage:
    Python scripts/mine.py --repo .cache/repos/click --limit 20
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_DIFF_LINES = 400


@dataclass(frozen=True)
class Candidate:
    commit: str
    parent: str
    date: str
    subject: str
    body: str
    files: list[str]
    test_files: list[str]
    source_files: list[str]
    added: int
    deleted: int


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _is_test(path: str) -> bool:
    lowered = path.lower()
    return (
        lowered.endswith("_test.py")
        or "/test_" in lowered
        or lowered.startswith("test_")
        or "/tests/" in lowered
        or lowered.startswith("tests/")
        or "/testing/" in lowered
    )


def _is_source(path: str) -> bool:
    return path.endswith(".py") and not _is_test(path)


def mine(repo: Path, limit: int) -> list[Candidate]:
    log = _git(
        repo,
        "log",
        "--no-merges",
        "--format=%H%x09%P%x09%ad%x09%s",
        "--date=short",
        "-n",
        str(limit * 10),
    )
    results: list[Candidate] = []
    for line in log.splitlines():
        commit, parents, date, subject = line.split("\t", 3)
        parent_list = parents.split()
        if len(parent_list) != 1:
            continue
        parent = parent_list[0]

        body = _git(repo, "show", "-s", "--format=%B", commit).strip()
        numstat = _git(repo, "show", "--numstat", "--format=", commit)
        files: list[str] = []
        added = deleted = 0
        for row in numstat.splitlines():
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            add_raw, del_raw, path = parts
            files.append(path)
            if add_raw.isdigit():
                added += int(add_raw)
            if del_raw.isdigit():
                deleted += int(del_raw)

        test_files = [p for p in files if _is_test(p)]
        source_files = [p for p in files if _is_source(p)]
        if not test_files or not source_files:
            continue
        if added + deleted > MAX_DIFF_LINES:
            continue

        results.append(
            Candidate(
                commit=commit,
                parent=parent,
                date=date,
                subject=subject,
                body=body,
                files=files,
                test_files=test_files,
                source_files=source_files,
                added=added,
                deleted=deleted,
            )
        )
        if len(results) >= limit:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        print(f"not a git clone: {repo}")
        return 1

    candidates = mine(repo, args.limit)
    if args.json:
        print(
            json.dumps(
                [asdict(candidate) for candidate in candidates],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    for index, candidate in enumerate(candidates, 1):
        print(
            f"[{index}] {candidate.commit[:10]} "
            f"{candidate.date} "
            f"+{candidate.added}/-{candidate.deleted} "
            f"{candidate.subject}"
        )
        print(f"    tests: {candidate.test_files}")
        print(f"    src:   {candidate.source_files}")
    print(f"--- {len(candidates)} candidates ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())