#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
from typing import List
from .base_runner import OsRunnerBase


class Anolis23Runner(OsRunnerBase):
    """
    针对 AnolisOS 23 的简单实现示例。
    假设容器里有 dnf/yum。
    """

    def __init__(self):
        # 简单检测一下 /etc/os-release
        os_release = self._read_os_release()
        name = os_release.get("NAME", "")
        version_id = os_release.get("VERSION_ID", "")
        print(f"[build-runner] Detected OS: {name} {version_id}")

        if "Anolis" not in name or not version_id.startswith("23"):
            print("[build-runner] WARNING: Anolis23Runner used on non-Anolis23 OS", file=sys.stderr)

    def _read_os_release(self) -> dict:
        result = {}
        if os.path.isfile("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"')
                    result[k] = v
        return result

    def _dnf_or_yum(self) -> str:
        # 优先 dnf，没有则用 yum
        if shutil.which("dnf"):
            return "dnf"
        return "yum"

    def install_system_packages(self, packages: List[str]):
        if not packages:
            return

        pkg_mgr = None
        if shutil.which("dnf"):
            pkg_mgr = "dnf"
        elif shutil.which("yum"):
            pkg_mgr = "yum"
        else:
            print("[build-runner] ERROR: neither dnf nor yum found in container.", file=sys.stderr)
            sys.exit(1)

        print(f"[build-runner] Installing system packages via {pkg_mgr}: {' '.join(packages)}")
        # -y 非交互安装，逐个安装包
        for package in packages:
            subprocess.check_call(f"{pkg_mgr} install -y {package}", shell=True)

    def install_node(self, version: str):
        """
        简单示例：根据 version 选择 nodejs 包。
        现实中可换成 nvm/asdf 或者企业内部 node 安装脚本。
        """
        print(f"[build-runner] Installing Node.js version spec: {version}")
        # 示例策略：如果 version 以 "18" 开头，就装 nodejs18，否则装默认 nodejs
        # 实际应根据 Anolis 23 仓库中的包命名调整
        if version.startswith("18"):
            pkg = "nodejs"  # 假设仓库中默认就是 nodejs 18
        else:
            pkg = "nodejs"
        self.install_system_packages([pkg])

    def install_rust(self, version: str):
        """
        简单示例：使用发行版自带 rust 包，不精确到 minor 版本。
        实际可根据 version 决定是否使用发行版包或 rustup。
        """
        print(f"[build-runner] Installing Rust version spec: {version}")
        # 示例策略：无论 version 为何，先安装发行版 rust/cargo
        # 未来你可以根据 version 调用 rustup 安装特定版本
        self.install_system_packages(["rust", "cargo"])
