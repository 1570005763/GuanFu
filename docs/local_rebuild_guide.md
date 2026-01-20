# 本地重复构建指南

本指南将介绍如何使用 GuanFu 在本地环境中基于指定的 `buildspec.yaml` 文件实现重复构建。

## 前提条件

在开始本地重复构建之前，请确保您已满足以下条件：

1. 已安装 Docker
2. 已安装 Python 3.7+
3. 已安装 Git
4. 已安装 YAML 解析器（PyYAML 包）

## 使用方法

GuanFu 提供了 `build-runner.sh` 脚本，默认以容器模式运行，即启动容器并在容器中运行构建。

使用方式非常简单：

```bash
# 在 GuanFu 项目根目录下运行
./src/build-runner.sh path/to/your/buildspec.yaml
```

如果未指定 `buildspec.yaml` 文件路径，则默认使用 `buildspec.yaml`。

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
