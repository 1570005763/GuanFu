# 在其他仓库中使用 GuanFu 构建工作流模板

本指南将介绍如何在其他仓库中调用 GuanFu 仓库定义的构建工作流模板。

## 概述

GuanFu 提供了一个可重用的构建工作流模板，允许其他仓库通过简单的配置来使用一致的构建环境和流程。

## 使用方法

### 1. 创建工作流文件

在您的仓库中创建一个工作流文件（例如 `.github/workflows/build.yml`），例如：

```yaml
name: Reproducible Build with GuanFu

on:
  push:
    tags:
      - 'v*'
  release:
    types: [published, created, edited]

jobs:
  build:
    uses: 1570005763/GuanFu/.github/workflows/build-template.yml@main
    with:
      spec_path: .buildspec.yaml
```

### 2. 配置参数

工作流模板支持以下参数：

- `spec_path`: 指定 buildspec.yaml 文件的路径（默认为 `.buildspec.yaml`）

```yaml
jobs:
  build:
    uses: 1570005763/GuanFu/.github/workflows/build-template.yml@main
    with:
      spec_path: path/to/your/buildspec.yaml
```

### 3. 准备 buildspec.yaml 文件

在您的仓库中创建 `buildspec.yaml` 文件（或指定的其他路径），文件应包含以下内容：

```yaml
# 容器配置（指定构建环境）
container:
  image: registry.example.com/build-base:anolis23-rust-node  # 构建环境镜像

# 输入资源（可选）
inputs:
  - name: source_code
    url: https://example.com/source.tar.gz
    sha256: abc123...
    targetDir: ./source

# 环境配置
environment:
  systemPackages:
    - name: gcc
      version: "11.2.0"
    - name: make
      version: "4.3"
    - name: git
      version: "2.30.0"
  tools:
    - name: node
      version: "18"
    - name: rust
      version: "1.70"

# 构建阶段
phases:
  prepare:
    commands:
      - echo "准备阶段命令"
      - mkdir -p build
  build:
    commands:
      - echo "构建阶段命令"
      - make build
```

## 详细配置说明

### 容器配置

`container.image` 指定构建所用的 Docker 镜像，这决定了构建环境的操作系统和预装工具。

### 输入资源 (inputs)

定义构建过程中需要下载的外部资源，包括：
- `name`: 资源名称
- `url`: 资源下载地址
- `sha256`: 可选，用于验证文件完整性的 SHA256 校验和
- `targetDir`: 资源解压或放置的目标目录

### 环境配置 (environment)

定义构建环境所需安装的包和工具：
- `systemPackages`: 系统包列表
- `tools`: 需要安装的工具及其版本

### 构建阶段 (phases)

定义构建过程中的不同阶段：
- `prepare`: 准备阶段，用于初始化环境
- `build`: 构建阶段，执行主要构建命令

## 注意事项

1. 确保 `buildspec.yaml` 文件格式正确
2. 指定的 Docker 镜像必须可访问
3. 构建命令应在指定的容器环境中能够正常运行
4. 如果需要使用仓库的 secrets，使用 `secrets: inherit`
5. 请使用适当的 Git 引用（如特定的提交哈希、标签或分支）以确保可重现性
