#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
from typing import List
from .base_runner import OsRunnerBase


class AnolisOSRunner(OsRunnerBase):
    """
    针对 AnolisOS 的简单实现示例。
    假设容器里有 dnf/yum。
    """

    def __init__(self):
        # 简单检测一下 /etc/os-release
        os_release = self._read_os_release()
        self.name = os_release.get("NAME", "")
        self.version_id = os_release.get("VERSION_ID", "")
        
        self.pkg_mgr = self._dnf_or_yum()
        
        print(f"[build-runner] Detected OS: {self.name} {self.version_id}")

        if "Anolis" not in self.name:
            print("[build-runner] WARNING: AnolisOSRunner used on non-AnolisOS OS", file=sys.stderr)

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
        elif shutil.which("yum"):
            return "yum"
        else:
            print("[build-runner] ERROR: neither dnf nor yum found in container.", file=sys.stderr)
            sys.exit(1)
    
    def _run_cmd(self, cmd: str) -> bool:
        """成功返回 True，失败返回 False。"""
        try:
            subprocess.check_call(cmd, shell=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"[build-runner] Command failed (exit {e.returncode}): {cmd}", file=sys.stderr)
            return False

    def _releasever_is_valid(self, releasever: str) -> bool:
        """
        判断某个 releasever 是否“有效”：
        尝试做一次轻量的 makecache，如果成功则认为该 releasever 可用。
        一旦失败，就认为是“无效 releasever”，后续更高小版本就不再尝试。
        """
        cmd = f"{self.pkg_mgr} -q -y makecache --releasever={releasever}"
        print(f"[build-runner] Checking releasever={releasever} with: {cmd}")
        return self._run_cmd(cmd)
    
    def _iter_releasevers(self):
        """
        生成要尝试的 releasever 序列。

        - 如果当前环境 VERSION_ID 以 "23" 开头（AnolisOS 23 系）：
            从 23.0 开始：23.0, 23.1, 23.2, ...
        - 如果当前环境 VERSION_ID 以 "8" 开头（RHEL8/alinux3/anolis8 等）：
            返回固定列表：
                8, 8.2, 8.4, 8.6, 8.8, 8.9, 8.10
        """

        vid = (self.version_id or "").strip()

        # Anolis OS 23.x：递增 23.0, 23.1, 23.2, ...
        if vid.startswith("23"):
            major = 23
            minor = 0
            max_minor = 100
            while minor <= max_minor:
                yield f"{major}.{minor}"
                minor += 1
            # 超过 23.100 就停止，不再 yield 更多

        # Anolis OS 8.x
        elif vid.startswith("8"):
            fixed_list = ["8", "8.2", "8.4", "8.6", "8.8", "8.9", "8.10"]
            for rv in fixed_list:
                yield rv

        else:
            # 不返回任何候选（上层循环不会进入）
            return
            
    def _install_one_package(self, package: str):
        """
        安装单个包：
          1. 先尝试不用 --releasever（当前系统默认行为）
          2. 如果失败，再从 23.0 起依次递增 releasever：
             - 对每个 releasever：
               a) 先 _releasever_is_valid：无效则停止递增；
               b) 有效则尝试 install，成功即结束。
          3. 全部失败则抛异常。
        """
        # 1) 不带 --releasever 的默认安装
        default_cmd = f"{self.pkg_mgr} install -y {package}"
        print(f"[build-runner] Trying install without releasever: {default_cmd}")
        if self._run_cmd(default_cmd):
            return

        # 2) 从 23.0 起递增 releasever
        for rv in self._iter_releasevers():
            # 先判断这个 releasever 是否可用
            if not self._releasever_is_valid(rv):
                print(
                    f"[build-runner] releasever={rv} seems invalid; "
                    f"stop trying higher minors for package '{package}'.",
                    file=sys.stderr,
                )
                break  # 第一个无效 releasever 出现就结束递增

            cmd = f"{self.pkg_mgr} install -y {package} --releasever={rv}"
            print(
                f"[build-runner] Trying install with releasever={rv}: {cmd}"
            )
            if self._run_cmd(cmd):
                return

        # 全都失败
        raise RuntimeError(
            f"[build-runner] Failed to install package '{package}' "
            f"using default repos and auto-incremented 23.x releasevers."
        )
        
    def install_system_packages(self, packages: List[str]):
        if not packages:
            return

        print(f"[build-runner] Installing system packages via {self.pkg_mgr}: {' '.join(packages)}")
        # -y 非交互安装，逐个安装包
        for package in packages:
            self._install_one_package(package)

