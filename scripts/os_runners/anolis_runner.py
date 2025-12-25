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

