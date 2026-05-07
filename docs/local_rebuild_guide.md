# 本地重复构建指南

本指南将介绍如何使用 GuanFu 在本地环境中基于指定的 `buildspec.yaml` 文件实现重复构建。

## 前提条件

在开始本地重复构建之前，请确保您已满足以下条件：

1. 已安装 Docker
2. 已安装 Python 3.6+
3. 已安装 Git
4. 已安装 YAML 解析器（PyYAML 包）

## 使用方法

GuanFu 提供了 `build-runner.sh` 脚本，默认以容器模式运行，即启动容器并在容器中运行构建。

为了兼容既有用法，脚本入口会继续保留：

```bash
# 在 GuanFu 项目根目录下运行
./src/build-runner.sh path/to/your/buildspec.yaml
```

如果未指定 `buildspec.yaml` 文件路径，则默认使用 `buildspec.yaml`。

同时，GuanFu 也提供统一的 Python 命令行入口：

```bash
python3 -m pip install -e .
guanfu rebuild buildspec --spec path/to/your/buildspec.yaml
```

`guanfu rebuild buildspec` 当前会薄包装既有 `build-runner.sh` 逻辑，因此两种方式的构建行为保持一致。

脚本会：
- 从 buildspec.yaml 文件中读取容器镜像配置
- 解析 buildspec.yaml 中的 outputs 部分，将输出文件目录挂载到容器中
- 将 buildspec.yaml 文件和 scripts 目录挂载到容器中
- 启动相应的容器并在其中执行构建
- 构建完成后，输出文件会自动保存到宿主机对应目录中

## 构建过程

当运行 `build-runner.sh` 脚本时，将在容器内执行以下步骤：

1. 确保容器内有 python3 和 pip
2. 安装 PyYAML（如果尚未安装）
3. 运行 `run_build.py` 脚本来执行实际的构建过程

`run_build.py` 脚本会按以下顺序执行：

1. 处理 inputs（下载/解压/克隆/检查）
2. 配置默认环境参数
3. 根据 environment 部分安装系统包和工具
4. 执行 phases 中定义的构建阶段命令

## OpenAnolis Koji RPM rebuild

GuanFu 还提供本地 Koji RPM rebuild 子命令，用于验证已经发布出来的 OpenAnolis RPM：

```bash
guanfu rebuild koji-rpm \
  --rpm-name zlib-1.2.13-3.an23.x86_64.rpm
```

该流程会：

1. 根据 RPM 名称查询 OpenAnolis Koji，定位 buildroot 和构建任务
2. 从发布 source repo 获取 SRPM，并下载 Koji task SRPM/logs 做同源校验
3. 使用 Koji `buildroot_id` 生成 `mock.cfg`
4. 使用 `mock --rebuild` 在本地重建发布 SRPM
5. 对比发布 RPM 和本地 rebuild RPM，并输出 `report.json`

`report.json` 会包含轻量差异摘要：

- RPM header 字段差异，例如 `BUILDTIME`、`BUILDHOST`、`PAYLOADDIGEST`、`RSAHEADER`
- 文件清单差异，例如只存在于发布 RPM 或 rebuild RPM 的文件
- 文件属性差异，例如 `mtime`、`digest`、`size`、`mode`、owner/group
- 汇总判断，例如是否仅有 mtime 差异、是否存在内容相关差异

默认 Koji 配置：

```text
koji server: https://build.openanolis.cn/kojihub
koji topurl: https://build.openanolis.cn/kojifiles
source RPM base URL: https://mirrors.openanolis.cn/anolis/23/os/source/Packages/
binary RPM base URL: https://mirrors.openanolis.cn/anolis/23/os/x86_64/os/Packages/
```

Koji RPM rebuild 需要 Linux 环境，并安装 `koji`、`mock`、`rpm` 和 `dnf`。macOS 不能原生运行 `mock`，需要 Linux 虚拟机或远程 Linux 主机。
