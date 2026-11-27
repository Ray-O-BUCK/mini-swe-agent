"""Sandbox 实现与工厂。"""
from __future__ import annotations

from pathlib import Path

from .base import Sandbox, SandboxError
from .docker import DEFAULT_IMAGE, DockerSandbox
from .local import LocalSandbox

DEFAULT_DOCKER_IMAGE = DEFAULT_IMAGE


def create_sandbox(workspace: Path, kind : str = "local", image: str = DEFAULT_DOCKER_IMAGE) -> Sandbox:
    if kind == "local":
        return LocalSandbox(workspace)
    elif kind == "docker":
        return DockerSandbox(workspace, image)
    else:
        raise ValueError(f"unknown sandbox kind: {kind!r}")
