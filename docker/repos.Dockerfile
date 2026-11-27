FROM sweagent-sandbox:py311

# 基础镜像已切到 agent 用户；装依赖要临时回到 root。
USER root
RUN pip install --no-cache-dir \
    urllib3 certifi idna charset_normalizer \
    tomli tomli_w \
    pytest-httpbin httpbin trustme pytest-mock \
    hypothesis pytest-mypy-plugins \
    pretend pytest-xdist
USER agent