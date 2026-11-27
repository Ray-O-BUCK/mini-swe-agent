FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep bash \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户 + 固定 uid 1000，方便和宿主 bind mount 对齐。
RUN pip install --no-cache-dir pytest \
    && useradd --no-create-home --uid 1000 --shell /bin/bash agent

# 非 root 访问 bind mount 的 .git 时，git 需要显式信任工作区。
RUN git config --system --add safe.directory /workspace

ENV PYTHONPATH=/workspace/src:/workspace
# rootfs 只读时 HOME 必须可写，否则 git/pytest 写缓存会失败。
ENV HOME=/tmp
ENV XDG_CACHE_HOME=/tmp/.cache
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
USER agent