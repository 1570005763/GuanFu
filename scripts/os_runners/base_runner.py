#!/usr/bin/env python3
import os
import sys
from typing import Dict, Any, Optional


class OsRunnerBase:
    """抽象 OS Runner 基类"""

    def install_system_packages(self, packages):
        raise NotImplementedError

    def install_node(self, version: str):
        raise NotImplementedError

    def install_rust(self, version: str):
        raise NotImplementedError


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

    # 需要延迟导入 Anolis23Runner 以避免循环导入
    from .anolis_runner import Anolis23Runner
    
    if "Anolis" in name and version_id.startswith("23"):
        return Anolis23Runner()
    else:
        return UnsupportedOsRunner(
            reason=f"OS not supported by build-runner (NAME={name}, VERSION_ID={version_id})."
        )
