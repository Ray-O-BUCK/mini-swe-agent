# 沙箱边界

## 威胁模型

| 风险                | LocalSandbox       | DockerSandbox                                     |
| :------------------ | :----------------- | :------------------------------------------------ |
| 误删工作区外文件    | 路径守卫（不完整） | 容器内，宿主不可见                                |
| 网络外联 / 下载依赖 | 不隔离             | `--network none`                                  |
| 资源耗尽            | 超时               | 1g 内存、1 CPU、128 pids                          |
| 读取宿主密钥        | 白名单 env         | 不传密钥；独立环境                                |
| 提权                | 不防               | `--cap-drop ALL` + `no-new-privileges`            |
| 写系统目录          | 不防               | `--read-only` rootfs，仅 `/tmp` 和 workspace 可写 |
| 内核漏洞逃逸        | 不防               | 不承诺防；这不是生产级多租户沙箱                  |

## 容器启动参数

- `--network none`
- `--memory 1g --memory-swap 1g`
- `--cpus 1`
- `--pids-limit 128`
- `--cap-drop ALL`
- `--security-opt no-new-privileges`
- `--read-only`
- `--tmpfs /tmp:rw,size=256m`
- `--user 1000:1000`
- 只挂载 workspace 到 `/workspace`

## 明确不做的

- 不防容器内核漏洞逃逸；
- 不做多租户隔离；
- 不限制磁盘 I/O；
- Windows bind mount 下 UID 语义与 Linux 不完全一致。