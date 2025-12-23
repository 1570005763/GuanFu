#!/usr/bin/env python3
import os
import sys
import subprocess
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

class OsRunnerBase:
    """抽象 OS Runner 基类"""

    def install_system_packages(self, packages):
        raise NotImplementedError

    def install_node(self, version: str):
        raise NotImplementedError

    def install_rust(self, version: str):
        raise NotImplementedError


# 导入 Anolis23Runner
from anolis_runner import Anolis23Runner


class UnsupportedOsRunner(OsRunnerBase):
    """用于非实现 OS 的占位 Runner，直接报错"""

    def __init__(self, reason: str = "Unsupported OS"):
        print(f"[build-runner] ERROR: {reason}", file=sys.stderr)

    def install_system_packages(self, packages):
        print("[build-runner] System package installation not implemented for this OS.", file=sys.stderr)
        sys.exit(1)

    def install_node(self, version: str):
        print("[build-runner] Node installation not implemented for this OS.", file=sys.stderr)
        sys.exit(1)

    def install_rust(self, version: str):
        print("[build-runner] Rust installation not implemented for this OS.", file=sys.stderr)
        sys.exit(1)


def detect_os_runner(spec: Dict[str, Any]) -> OsRunnerBase:
    """
    根据 spec.container.image 或容器内 /etc/os-release 选择 OS-specific runner。
    当前仅实现 Anolis 23，其它一律 Unsupported。
    """
    container = spec.get("container", {})
    image = container.get("image", "")
    # 有两种方式：
    # 1) 根据 image 名字简单判断（比如包含 anolis23）
    # 2) 运行时读取 /etc/os-release
    # 这里我们做法是：优先根据 /etc/os-release 实际检测，
    # 若是 Anolis 23 则用 Anolis23Runner，否则 Unsupported。

    os_release = {}
    if os.path.isfile("/etc/os-release"):
        with open("/etc/os-release") as f:
            for line in f:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"')
                os_release[k] = v

    name = os_release.get("NAME", "")
    version_id = os_release.get("VERSION_ID", "")

    if "Anolis" in name and version_id.startswith("23"):
        return Anolis23Runner()
    else:
        return UnsupportedOsRunner(
            reason=f"OS not supported by build-runner (NAME={name}, VERSION_ID={version_id})."
        )


# ----------------------
# 处理 inputs
# ----------------------

def handle_inputs(spec: Dict[str, Any]) -> None:
    inputs = spec.get("inputs", {}) or {}

    for name, cfg in inputs.items():
        print(f"[build-runner] Handling input '{name}'")
        url = cfg.get("url")
        sha256 = cfg.get("sha256")
        target_dir = cfg.get("targetDir")

        if url and target_dir:
            handle_input_archive(name, cfg)
        else:
            print(f"[build-runner] WARNING: input '{name}' must specify url and targetDir.", file=sys.stderr)


def handle_input_archive(name: str, cfg: Dict[str, Any]) -> None:
    url = cfg.get("url")
    target_dir = cfg.get("targetDir")
    sha256 = cfg.get("sha256")

    if not url or not target_dir:
        print(f"[build-runner] ERROR: input '{name}' must specify url and targetDir.", file=sys.stderr)
        sys.exit(1)

    print(f"[build-runner]  - url={url}, targetDir={target_dir}")
    run(f"curl -L '{url}' -o /tmp/{name}.tar.gz")

    if sha256:
        print(f"[build-runner]  - verifying sha256")
        run(f"echo '{sha256}  /tmp/{name}.tar.gz' | sha256sum -c -")

    os.makedirs(target_dir, exist_ok=True)
    run(f"tar xf /tmp/{name}.tar.gz -C {target_dir} --strip-components=1")


# ----------------------
# 处理 environment（systemPackages + tools）
# ----------------------

def handle_environment(spec: Dict[str, Any], os_runner: OsRunnerBase) -> None:
    env_cfg = spec.get("environment", {}) or {}
    system_packages = env_cfg.get("systemPackages", []) or []
    tools_cfg = env_cfg.get("tools", {}) or {}

    # 1. 安装系统包
    if system_packages:
        # 转换为包名列表，只取 name 字段
        package_names = []
        for pkg in system_packages:
            if isinstance(pkg, dict) and 'name' in pkg:
                package_names.append(pkg['name'])
            elif isinstance(pkg, str):
                # 兼容旧格式，如果直接是字符串
                package_names.append(pkg)
        os_runner.install_system_packages(package_names)

    # 2. 安装工具
    # tools 现在也是对象列表，需要转换
    node_version = None
    rust_version = None
    
    for tool in tools_cfg:
        if isinstance(tool, dict) and 'name' in tool and 'version' in tool:
            if tool['name'] == 'node':
                node_version = tool['version']
            elif tool['name'] == 'rust':
                rust_version = tool['version']
    
    if node_version:
        os_runner.install_node(node_version)

    if rust_version:
        os_runner.install_rust(rust_version)

    # 未来可以继续扩展其它工具，如 python/java 等


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

    # 2. 根据 environment 安装系统包和工具
    handle_environment(spec, os_runner)

    # 3. 执行 phases
    handle_phases(spec)


if __name__ == "__main__":
    main()
