# 本地重复构建指南

本指南将介绍如何使用 GUANFU 在本地环境中基于指定的 `buildspec.yaml` 文件实现重复构建。

## 前提条件

在开始本地重复构建之前，请确保您已满足以下条件：

1. 已安装 Docker
2. 已安装 Python 3.7+
3. 已安装 Git

## 使用方法

GUANFU 提供了 `build-runner.sh` 脚本，默认以容器模式运行，即启动容器并在容器中运行构建。

使用方式非常简单：

```bash
# 在 GuanFu 项目根目录下运行
./scripts/build-runner.sh path/to/your/buildspec.yaml
```

如果未指定 `buildspec.yaml` 文件路径，则默认使用 `buildspec.yaml`。

脚本会：
- 从 buildspec.yaml 文件中读取容器镜像配置
- 启动相应的容器并在其中执行构建
