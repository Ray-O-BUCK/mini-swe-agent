"""Benchmark report: aggregate per-task results into JSON + Markdown."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

from ..types import Usage
from .evaluate import EvalResult


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    agent_status: str
    steps: int
    usage: Usage
    patch_chars: int
    eval_result: EvalResult
    run_dir: str
    duration_s: float


def write_report(results: list[TaskRunResult], out_dir: Path, metadata: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": metadata,
        "results": [asdict(result) for result in results],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    report_path = out_dir / "report.md"
    report_path.write_text(
        _render_markdown(results, metadata),
        encoding="utf-8",
        newline="\n",
    )
    return report_path


def _render_markdown(results: list[TaskRunResult], metadata: dict) -> str:
    total = len(results)
    resolved = sum(1 for result in results if result.eval_result.resolved)
    rate = resolved / total if total else 0.0
    steps = [result.steps for result in results]
    tokens = [result.usage.total_tokens for result in results]
    failures = Counter(result.eval_result.failure_type for result in results if not result.eval_result.resolved)

    lines = [
        "# Benchmark Report",
        "",
        f"- model: `{metadata.get('model')}`",
        f"- mode: `{metadata.get('mode')}`",
        f"- sandbox: `{metadata.get('sandbox')}`",
        f"- max_steps: {metadata.get('max_steps')}",
        f"- tasks: {total}",
        f"- resolved: {resolved}/{total} ({rate:.1%})",
        f"- steps median: {median(steps) if steps else 0}",
        f"- tokens median: {median(tokens) if tokens else 0}",
        "",
        "## Failures",
        "",
    ]
    if failures:
        for failure, count in failures.most_common():
            lines.append(f"- `{failure}`: {count}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "| Task | Agent | Steps | Prompt | Completion | Total | Patch | Resolved | Failure | Eval s |",
            "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :---: | :--- | ---: |",
        ]
    )
    for result in results:
        eval_result = result.eval_result
        lines.append(
            f"| {result.task_id} "
            f"| {result.agent_status} "
            f"| {result.steps} "
            f"| {result.usage.prompt_tokens} "
            f"| {result.usage.completion_tokens} "
            f"| {result.usage.total_tokens} "
            f"| {result.patch_chars} "
            f"| {'yes' if eval_result.resolved else 'no'} "
            f"| {eval_result.failure_type} "
            f"| {eval_result.duration_s:.2f} |"
        )
    lines.append("")
    return "\n".join(lines)
