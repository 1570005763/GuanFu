#!/usr/bin/env bash
set -euo pipefail

# 解析命令行参数
MODE="container"  # 默认模式为启动容器
BUILD_SPEC_FILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --mode|--Mode)
            MODE="$2"
            shift 2
            ;;
        *)
            BUILD_SPEC_FILE="$1"
            shift
            ;;
    esac
done

# 如果没有指定 buildspec 文件，则使用默认值
if [ -z "$BUILD_SPEC_FILE" ]; then
    BUILD_SPEC_FILE="buildspec.yaml"
fi

if [ ! -f "$BUILD_SPEC_FILE" ]; then
    echo "Error: Build spec file '$BUILD_SPEC_FILE' not found."
    exit 1
fi

# 根据模式决定运行方式
if [ "$MODE" = "direct" ] || [ "$MODE" = "Direct" ]; then
    # 直接运行模式（在容器内运行构建脚本）
    python3 /opt/build-system/run_build.py "$BUILD_SPEC_FILE"
else
    # 容器模式（默认）- 从宿主机启动容器运行
    # 从 buildspec.yaml 读取容器镜像
    if command -v python3 >/dev/null 2>&1; then
        CONTAINER_IMAGE=$(python3 -c "
import yaml, sys
with open('$BUILD_SPEC_FILE') as f:
    spec = yaml.safe_load(f)
print(spec.get('container', {}).get('image', 'anolis23-build-base:latest'))
" 2>/dev/null || echo "anolis23-build-base:latest")
    else
        echo "Error: python3 is required to parse the buildspec file."
        exit 1
    fi

    echo "Running build in container: $CONTAINER_IMAGE"
    docker run -v "$(pwd)":/workspace -w /workspace -e BUILD_IN_CONTAINER=1 "$CONTAINER_IMAGE" \
        /bin/bash -c "./scripts/build-runner.sh --mode direct $BUILD_SPEC_FILE"
fi
