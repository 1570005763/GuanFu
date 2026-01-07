#!/usr/bin/env bash
set -euo pipefail

print_logo() {
  cat << 'EOF'
_____/\\\\\\\\\\\\_____________________________________________/\\\\\\\\\\\\\\\_______________        
 ___/\\\//////////_____________________________________________\/\\\///////////________________       
  __/\\\________________________________________________________\/\\\___________________________      
   _\/\\\____/\\\\\\\__/\\\____/\\\__/\\\\\\\\\_____/\\/\\\\\\___\/\\\\\\\\\\\______/\\\____/\\\_     
    _\/\\\___\/////\\\_\/\\\___\/\\\_\////////\\\___\/\\\////\\\__\/\\\///////______\/\\\___\/\\\_    
     _\/\\\_______\/\\\_\/\\\___\/\\\___/\\\\\\\\\\__\/\\\__\//\\\_\/\\\_____________\/\\\___\/\\\_   
      _\/\\\_______\/\\\_\/\\\___\/\\\__/\\\/////\\\__\/\\\___\/\\\_\/\\\_____________\/\\\___\/\\\_  
       _\//\\\\\\\\\\\\/__\//\\\\\\\\\__\//\\\\\\\\/\\_\/\\\___\/\\\_\/\\\_____________\//\\\\\\\\\__ 
        __\////////////_____\/////////____\////////\//__\///____\///__\///_______________\/////////___

    GuanFu Reproducible Build - v1.1.0
EOF
}

print_logo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 解析命令行参数
BUILD_SPEC_FILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
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
export BUILD_SPEC_FILE

# 调用校验脚本验证buildspec文件中outputs的path是否都是绝对路径
echo "正在校验buildspec文件..."
if ! python3 "$SCRIPT_DIR/validate_buildspec.py" "$BUILD_SPEC_FILE"; then
    echo "Error: Build spec validation failed. Outputs path must be absolute paths."
    exit 1
fi
echo "Build spec validation passed."

# 检查是否可用 python3 命令
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required to parse the buildspec file."
    exit 1
fi

# 从 buildspec.yaml 读取容器镜像
CONTAINER_IMAGE=$(python3 - << 'PYEOF'
import yaml, sys, os
spec_path = os.environ["BUILD_SPEC_FILE"]
with open(spec_path) as f:
    spec = yaml.safe_load(f)
print(spec.get('container', {}).get('image', 'anolis23-build-base:latest'))
PYEOF
)

# 提前获取 buildspec.yaml 中定义的 outputs 信息，准备挂载输出文件
# 读取 outputs 部分并生成挂载参数
OUTPUT_PATHS=$(python3 - << 'PYEOF'
import yaml, sys, os
spec_path = os.environ["BUILD_SPEC_FILE"]
with open(spec_path) as f:
    spec = yaml.safe_load(f)
outputs = spec.get('outputs', [])
for output in outputs:
    if isinstance(output, dict) and 'path' in output:
        print(output['path'])
PYEOF
)

# 为每个输出路径创建挂载参数
OUTPUT_MOUNTS=""
if [ -n "$OUTPUT_PATHS" ]; then
    while IFS= read -r output_path; do
        if [ -n "$output_path" ]; then
            # 获取输出文件的目录部分，用于挂载
            output_dir=$(dirname "$output_path")
            # 创建宿主机目录以确保挂载成功
            mkdir -p "$output_dir" 2>/dev/null || true
            # 添加挂载参数（output_dir 已经是绝对路径）
            OUTPUT_MOUNTS="$OUTPUT_MOUNTS -v $output_dir:$output_dir"
        fi
    done <<< "$OUTPUT_PATHS"
fi

# 获取BUILD_SPEC_FILE绝对路径
[[ "$BUILD_SPEC_FILE" != /* ]] && BUILD_SPEC_FILE="$PWD/$BUILD_SPEC_FILE"

# 输出容器运行配置信息
echo "==========================================="
echo "Container Build Configuration:"
echo "  Running build in container: $CONTAINER_IMAGE"
echo "  Build spec file: $BUILD_SPEC_FILE"
echo "  Output mounts: $OUTPUT_MOUNTS"
echo "==========================================="

# docker run --rm $OUTPUT_MOUNTS -v "$BUILD_SPEC_FILE:/opt/build-system/buildspec.yaml" -v "$SCRIPT_DIR:/opt/build-system/scripts" "$CONTAINER_IMAGE" \
#     /bin/bash -c "cd /opt/build-system && python3 scripts/run_build.py buildspec.yaml"
docker run --rm $OUTPUT_MOUNTS \
  -v "$BUILD_SPEC_FILE:/opt/build-system/buildspec.yaml" \
  -v "$SCRIPT_DIR:/opt/build-system/scripts" \
  "$CONTAINER_IMAGE" \
  /bin/bash -c '
    set -e

    echo "[container] Ensuring python3 and pip are available..."

    install_python3_and_pip() {
      if command -v apt-get >/dev/null 2>&1; then
        # Debian / Ubuntu
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip
      elif command -v dnf >/dev/null 2>&1; then
        # RHEL / CentOS / Anolis / Rocky / Alma / Fedora 等
        dnf install -y python3 python3-pip
      elif command -v yum >/dev/null 2>&1; then
        # 老 RHEL / CentOS
        yum install -y python3 python3-pip
      elif command -v zypper >/dev/null 2>&1; then
        # openSUSE / SLES
        zypper refresh
        zypper install -y python3 python3-pip
      elif command -v pacman >/dev/null 2>&1; then
        # Arch
        pacman -Sy --noconfirm python python-pip
      else
        echo "[container] No known package manager; please bake python3+pip into the image." >&2
        exit 1
      fi
    }

    # 1. python3
    if ! command -v python3 >/dev/null 2>&1; then
      install_python3_and_pip
    else
      echo "[container] python3: $(python3 --version)"
      # 1.1 确保 pip 也有
      if ! python3 -m pip --version >/dev/null 2>&1; then
        echo "[container] python3 present but pip missing, installing pip..."
        install_python3_and_pip
      fi
    fi

    # 2. PyYAML
    if ! python3 -c "import yaml" >/dev/null 2>&1; then
      echo "[container] Installing PyYAML via pip..."
      python3 -m pip install --upgrade pip
      python3 -m pip install PyYAML
    else
      echo "[container] PyYAML already installed."
    fi

    # 3. 运行真正的构建脚本
    cd /opt/build-system
    python3 scripts/run_build.py buildspec.yaml
  '
