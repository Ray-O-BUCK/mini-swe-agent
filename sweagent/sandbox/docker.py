from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

from ..types import ExecResult
from .base import SandboxError
from .local import LocalSandbox

DEFAULT_IMAGE = "sweagent-sandbox:py311"
CONTAINER_WORKSPACE = "/workspace"
HARDENED_RUN_ARGS = [
    "--network", "none",
    "--memory", "1g",
    "--memory-swap", "1g",
    "--cpus", "1",
    "--pids-limit", "128",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--read-only",
    "--tmpfs", "/tmp:rw,size=256m",
    "--user", "1000:1000",
]


def build_container_args(
    name: str,
    image: str,
    workspace: Path
) -> list[str]:
    """构造 docker run 参数。

        抽成纯函数是为了单测能在不启动容器的情况下检查安全参数。
        """
    return [
         "docker", "run", "-d",
        "--name", name,
        *HARDENED_RUN_ARGS,
        "-v", f"{workspace}:{CONTAINER_WORKSPACE}",
        "-w", CONTAINER_WORKSPACE,
        image,
        "sleep", "infinity",
    ]


class DockerSandbox(LocalSandbox):
    def __init__(self, workspace: Path, image: str = DEFAULT_IMAGE) -> None:
        super().__init__(workspace)
        self._image = image
        self._container_name = f"sweagent-{uuid.uuid4().hex[:10]}"
        self._start_container()

    def _start_container(self) -> None:
        completed = subprocess.run(
            build_container_args(
                self._container_name,
                self._image,
                self._root,
            ),
            capture_output=True,
        )
        if completed.returncode !=0:
            detail = completed.stderr.decode("utf-8",errors = "replace").strip()
            raise SandboxError(
                "failed to start docker container: "
                f"{detail}\n"
                "hint: docker build -t "
                f"{self._image} -f docker/sandbox.Dockerfile ."
            )

    def exec(self, command: str, timeout: int = 60) -> ExecResult:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-w",
                    CONTAINER_WORKSPACE,
                    self._container_name,
                    "bash",
                    "-lc",
                    "set -o pipefail; " + command,
                ],
                capture_output = True,
                timeout = timeout,
            )
            return ExecResult(
                command=command,
                exit_code=completed.returncode,
                stdout=_decode(completed.stdout),
                stderr=_decode(completed.stderr),
                duration_ms=int(
                    (time.perf_counter() - started) * 1000
                ),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                command=command,
                exit_code=-1,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                duration_ms=int(
                    (time.perf_counter() - started) * 1000
                ),
                timed_out=True,
            )
        except OSError as exc:
            raise SandboxError(f"docker exec failed: {exc}") from exc

    def close(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self._container_name],
            capture_output=True,
        )


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value