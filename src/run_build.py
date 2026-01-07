#!/usr/bin/env python3
import os
import sys
import subprocess
import urllib.parse
import yaml
from typing import Dict, Any, Optional

# ----------------------
# 通用工具函数
# ----------------------


def run(cmd: str, env: Optional[Dict[str, str]] = None) -> None:
    print(f"+ {cmd}")
    subprocess.check_call(cmd, shell=True, env=env)


def ensure_file(path: str) -> None:
    if not os.path.isfile(path):
        print(f"[build-runner] Spec file not found: {path}", file=sys.stderr)
        sys.exit(1)


def load_spec(spec_path: str) -> Dict[str, Any]:
    with open(spec_path) as f:
        return yaml.safe_load(f)


# ----------------------
# OS 类型检测 & OS-specific runner 选择
# ----------------------

from os_runners import OsRunnerBase, detect_os_runner


# ----------------------
# 处理 inputs
# ----------------------

def handle_inputs(spec: Dict[str, Any]) -> None:
    inputs = spec.get("inputs", {}) or {}

    for name, cfg in inputs.items():
        print(f"[build-runner] Handling input '{name}'")
        url = cfg.get("url")
        sha256 = cfg.get("sha256")
        target_path = cfg.get("targetPath")

        if not url or not target_path:
            print(f"[build-runner] ERROR: input '{name}' must specify url and targetPath.", file=sys.stderr)
            sys.exit(1)
        
        print(f"[build-runner]  - url={url}, targetPath={target_path}")
        run(f"mkdir -p \"$(dirname '{target_path}')\"")
        run(f"curl -L -o '{target_path}' '{url}'")

        if sha256:
            print(f"[build-runner]  - verifying sha256")
            run(f"echo '{sha256}  {target_path}' | sha256sum -c -")


# ----------------------
# 处理 environment（default + systemVariables + systemPackages + tools）
# ----------------------

def setup_default_environment() -> None:
    # Set default environment variables
    os.environ['LANG'] = 'C.UTF-8'
    os.environ['LC_ALL'] = 'C.UTF-8'
    os.environ['TZ'] = 'UTC'
    os.environ['SOURCE_DATE_EPOCH'] = '1717020800'
    os.environ['RPM_BUILD_NCPUS'] = '1'
    
    # Setup RPM macros for reproducible builds
    rpm_macros_content = """%build_mtime_policy clamp_to_source_date_epoch
%clamp_mtime_to_source_date_epoch 1
%use_source_date_epoch_as_buildtime 1
%_buildhost reproducible
"""
    with open('/etc/rpm/macros.buildroot', 'w') as f:
        f.write(rpm_macros_content)
    
    # Setup Rust configuration for reproducible builds and multi-arch support
    rust_config_content = """[build]
# 全局 rustflags，先留空，由 per-target 覆盖
rustflags = []

# 各主流架构的 target 配置
# 1) x86_64 Linux（包括多数 Anolis / RHEL / Debian / Ubuntu x86_64）
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=x86-64",
]

[target.x86_64-unknown-linux-musl]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=x86-64",
]

# 2) AArch64 (arm64) Linux
[target.aarch64-unknown-linux-gnu]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=generic",
]

[target.aarch64-unknown-linux-musl]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=generic",
]

# 3) ARMv7 (32-bit arm, hard float)
[target.armv7-unknown-linux-gnueabihf]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=generic",
]

[target.armv7-unknown-linux-musleabihf]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=generic",
]

# 4) RISC-V 64
[target.riscv64gc-unknown-linux-gnu]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=generic",
]

[target.riscv64gc-unknown-linux-musl]
linker = "clang"
rustflags = [
#     "-C", "link-arg=-fuse-ld=lld",
    "-C", "target-cpu=generic",
]

# Release Profile（面向可重现构建）
[profile.release]
codegen-units = 1
lto = "fat"
debug = 1"""
    
    # Create directory if it doesn't exist
    os.makedirs('/root/.cargo', exist_ok=True)
    
    # Write the Rust configuration
    with open('/root/.cargo/config.toml', 'w') as f:
        f.write(rust_config_content)

def handle_environment(spec: Dict[str, Any], os_runner: OsRunnerBase) -> None:
    env_cfg = spec.get("environment", {}) or {}
    system_packages = env_cfg.get("systemPackages", []) or []
    tools = env_cfg.get("tools", []) or []
    variables = env_cfg.get("variables", []) or []

    # 1. 处理环境变量
    for var in variables:
        if isinstance(var, dict) and 'name' in var and 'value' in var:
            name = var['name']
            value = str(var['value'])
            os.environ[name] = value
            print(f"[build-runner] Set environment variable: {name}={value}")

    # 2. 安装系统包
    if system_packages:
        # 转换为包列表，支持 name 或 {name, version} 格式
        package_list = []
        for pkg in system_packages:
            if isinstance(pkg, dict) and 'name' in pkg:
                if 'version' in pkg:
                    # 如果有版本信息，创建 name-version 格式的包名
                    package_spec = f"{pkg['name']}-{pkg['version']}"
                    package_list.append(package_spec)
                else:
                    package_list.append(pkg['name'])
        os_runner.install_system_packages(package_list)

    # 3. 安装工具
    if tools:
        # 转换为包列表，支持 name 或 {name, version} 格式
        tool_list = []
        for tool in tools:
            if isinstance(tool, dict) and 'name' in tool:
                if 'version' in tool:
                    # 如果有版本信息，创建 name-version 格式的包名
                    tool_spec = f"{tool['name']}-{tool['version']}"
                    tool_list.append(tool_spec)
                else:
                    tool_list.append(tool['name'])
        os_runner.install_system_packages(tool_list)


# ----------------------
# 处理 phases
# ----------------------

def handle_phases(spec: Dict[str, Any]) -> None:
    phases = spec.get("phases", {}) or {}
    for phase_name in ["prepare", "build"]:
        phase = phases.get(phase_name)
        if not phase:
            continue
        print(f"[build-runner] === Phase: {phase_name} ===")
        commands = phase.get("commands", []) or []
        for cmd in commands:
            run(cmd)


# ----------------------
# main
# ----------------------

def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else ".buildspec.yaml"
    ensure_file(spec_path)

    spec = load_spec(spec_path)

    # 选择 OS-specific runner
    os_runner = detect_os_runner(spec)

    # 1. 处理 inputs（下载/解压/clone/检查）
    handle_inputs(spec)
    
    # 2-1. 配置默认的环境参数
    setup_default_environment()

    # 2-2. 根据 environment 安装系统包和工具
    handle_environment(spec, os_runner)

    # 3. 执行 phases
    handle_phases(spec)


if __name__ == "__main__":
    main()

