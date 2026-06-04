#!/usr/bin/env python3
import os
import sys
import re
import hashlib
import shutil
import subprocess
import urllib.parse
import yaml
from typing import Dict, Any, Optional, List, Tuple

# ----------------------
# 通用工具函数
# ----------------------

DEFAULT_RPM_SOURCE_PAYLOAD = "w9.gzdio"
SYSTEM_DEFAULT_RPM_SOURCE_PAYLOAD = "system-default"
PAYLOAD_FLAG_RE = re.compile(r"^w([A-Za-z0-9+]*)\.(gzdio|bzdio|xzdio|lzdio|zstdio|ufdio)$")
PAYLOAD_COMPRESSOR_BY_TYPE = {
    "gzdio": "gzip",
    "bzdio": "bzip2",
    "xzdio": "xz",
    "lzdio": "lzma",
    "zstdio": "zstd",
    "ufdio": "(none)",
}
RPM_TOOLCHAIN_PACKAGES = ["rpm-build", "rpm-libs", "zlib", "xz-libs", "libzstd", "zstd"]
RPM_TOOLCHAIN_PACKAGE_NAMES = set(RPM_TOOLCHAIN_PACKAGES)


class BuildRunnerError(RuntimeError):
    pass


def run(cmd: str, env: Optional[Dict[str, str]] = None) -> None:
    print(f"+ {cmd}")
    
    bashrc = os.path.expanduser("~/.bashrc")
    if os.path.isfile(bashrc):
        bash_cmd = f'source {bashrc} >/dev/null 2>&1; {cmd}'
    else:
        bash_cmd = cmd
    
    subprocess.check_call(["bash", "-c", bash_cmd], env=env)


def ensure_file(path: str) -> None:
    if not os.path.isfile(path):
        print(f"[build-runner] Spec file not found: {path}", file=sys.stderr)
        sys.exit(1)


def load_spec(spec_path: str) -> Dict[str, Any]:
    with open(spec_path) as f:
        return yaml.safe_load(f)


def _run_capture(cmd: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def validate_rpm_source_payload(value: str) -> str:
    if value == SYSTEM_DEFAULT_RPM_SOURCE_PAYLOAD:
        return value
    match = PAYLOAD_FLAG_RE.match(value or "")
    if not match:
        raise BuildRunnerError(
            "invalid GUANFU_RPM_SOURCE_PAYLOAD '%s'; expected '%s' or rpm payload flags like 'w9.gzdio'"
            % (value, SYSTEM_DEFAULT_RPM_SOURCE_PAYLOAD)
        )
    flags, payload_type = match.groups()
    if payload_type == "ufdio" and flags:
        raise BuildRunnerError("invalid GUANFU_RPM_SOURCE_PAYLOAD '%s'; ufdio must not use compression flags" % value)
    return value


def rpm_source_payload_from_env() -> str:
    value = os.environ.get("GUANFU_RPM_SOURCE_PAYLOAD", DEFAULT_RPM_SOURCE_PAYLOAD).strip()
    if not value:
        value = DEFAULT_RPM_SOURCE_PAYLOAD
    return validate_rpm_source_payload(value)


def expected_payload_header(payload: str) -> Optional[Tuple[str, str]]:
    if payload == SYSTEM_DEFAULT_RPM_SOURCE_PAYLOAD:
        return None
    match = PAYLOAD_FLAG_RE.match(payload)
    if not match:
        raise BuildRunnerError("invalid rpm source payload '%s'" % payload)
    flags, payload_type = match.groups()
    return PAYLOAD_COMPRESSOR_BY_TYPE[payload_type], flags


def build_rpm_macros_content(source_payload: str) -> str:
    lines = [
        "%build_mtime_policy clamp_to_source_date_epoch",
        "%clamp_mtime_to_source_date_epoch 1",
        "%use_source_date_epoch_as_buildtime 1",
        "%_buildhost reproducible",
    ]
    if source_payload != SYSTEM_DEFAULT_RPM_SOURCE_PAYLOAD:
        lines.append(f"%_source_payload {source_payload}")
    return "\n".join(lines) + "\n"


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
        
        if url.startswith("file:///"):
            # 本地文件已由 build-runner.sh 挂载到 targetPath，无需下载
            print(f"[build-runner]  - local file (mounted by build-runner.sh)")
            if not os.path.exists(target_path):
                print(f"[build-runner] ERROR: local file not mounted at '{target_path}'", file=sys.stderr)
                sys.exit(1)
        elif url.startswith("http://") or url.startswith("https://"):
            # 远程 URL，需要下载
            print(f"[build-runner]  - downloading from remote URL")
            run(f"mkdir -p \"$(dirname '{target_path}')\"")
            run(f"curl -L -o '{target_path}' '{url}'")
        else:
            print(f"[build-runner] ERROR: unsupported URL scheme in '{url}'. Use http://, https://, or file:///", file=sys.stderr)
            sys.exit(1)

        if sha256:
            print(f"[build-runner]  - verifying sha256")
            run(f"echo '{sha256}  {target_path}' | sha256sum -c -")


# ----------------------
# 处理 environment（default + systemVariables + systemPackages + tools）
# ----------------------

def setup_default_environment() -> str:
    # Set default environment variables
    os.environ['LANG'] = 'C.UTF-8'
    os.environ['LC_ALL'] = 'C.UTF-8'
    os.environ['TZ'] = 'UTC'
    os.environ['SOURCE_DATE_EPOCH'] = '1717020800'
    os.environ['RPM_BUILD_NCPUS'] = '1'

    source_payload = rpm_source_payload_from_env()
    print(f"[build-runner] RPM source payload policy: {source_payload}")
    
    # Setup RPM macros for reproducible builds
    rpm_macros_content = build_rpm_macros_content(source_payload)
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

    return source_payload


def _versioned_packages(items: List[Any]) -> List[Tuple[str, str]]:
    packages = []
    for item in items:
        if isinstance(item, dict) and item.get("name") and item.get("version"):
            packages.append((str(item["name"]), str(item["version"])))
    return packages


def declared_versioned_packages(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    env_cfg = spec.get("environment", {}) or {}
    packages = []
    packages.extend(_versioned_packages(env_cfg.get("systemPackages", []) or []))
    packages.extend(_versioned_packages(env_cfg.get("tools", []) or []))
    return packages


def _normalize_rpm_version_line(line: str) -> str:
    epoch, sep, version_release = line.strip().partition(":")
    if not sep:
        return line.strip()
    if epoch in ("", "(none)", "0"):
        return version_release
    return "%s:%s" % (epoch, version_release)


def query_installed_rpm_versions(name: str) -> List[str]:
    if not shutil.which("rpm"):
        raise BuildRunnerError("rpm command is not available; cannot verify package '%s'" % name)
    query = "%{EPOCH}:%{VERSION}-%{RELEASE}\n"
    code, out, err = _run_capture(["rpm", "-q", "--qf", query, name])
    if code != 0:
        raise BuildRunnerError("failed to query package '%s': %s" % (name, err or out))
    return [_normalize_rpm_version_line(line) for line in out.splitlines() if line.strip()]


def verify_declared_package_versions(packages: List[Tuple[str, str]]) -> None:
    packages = [(name, version) for name, version in packages if name in RPM_TOOLCHAIN_PACKAGE_NAMES]
    if not packages:
        return
    if not shutil.which("rpm"):
        print("[build-runner] WARNING: rpm is not available; skipping declared package version verification")
        return

    print("[build-runner] Verifying declared package versions...")
    for name, expected in packages:
        versions = query_installed_rpm_versions(name)
        if expected not in versions:
            raise BuildRunnerError(
                "package '%s' version mismatch: expected %s, installed %s"
                % (name, expected, ", ".join(versions) or "<not installed>")
            )
        print(f"[build-runner]  - {name}: {expected} OK")


def log_rpm_toolchain_state() -> None:
    if not shutil.which("rpm"):
        print("[build-runner] WARNING: rpm is not available; skipping rpm toolchain state logging")
        return

    print("[build-runner] RPM toolchain state:")
    code, out, err = _run_capture(["rpm", "--version"])
    if code == 0:
        print(f"[build-runner]  - {out}")
    else:
        print(f"[build-runner]  - rpm --version failed: {err or out}")

    code, out, err = _run_capture(["rpm", "--showrc"])
    if code == 0:
        digest = hashlib.sha256(out.encode("utf-8")).hexdigest()
        print(f"[build-runner]  - rpm --showrc sha256: {digest}")
    else:
        print(f"[build-runner]  - rpm --showrc failed: {err or out}")

    code, out, err = _run_capture(["rpm", "-q"] + RPM_TOOLCHAIN_PACKAGES)
    if out:
        for line in out.splitlines():
            print(f"[build-runner]  - {line}")
    if code != 0 and err:
        for line in err.splitlines():
            print(f"[build-runner]  - {line}")

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

    log_rpm_toolchain_state()
    verify_declared_package_versions(declared_versioned_packages(spec))


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


def _source_rpm_outputs(spec: Dict[str, Any]) -> List[str]:
    outputs = spec.get("outputs", []) or []
    paths = []
    for output in outputs:
        if isinstance(output, dict):
            path = output.get("path")
            if isinstance(path, str) and path.endswith(".src.rpm"):
                paths.append(path)
    return paths


def query_source_rpm_payload_header(path: str) -> Tuple[str, str]:
    query = "%{PAYLOADCOMPRESSOR}\n%{PAYLOADFLAGS}\n"
    code, out, err = _run_capture(["rpm", "-qp", "--queryformat", query, path])
    if code != 0:
        raise BuildRunnerError("failed to query source rpm payload for '%s': %s" % (path, err or out))
    lines = out.splitlines()
    compressor = lines[0].strip() if len(lines) >= 1 else ""
    flags = lines[1].strip() if len(lines) >= 2 else ""
    if flags == "(none)":
        flags = ""
    return compressor, flags


def verify_source_payload_outputs(spec: Dict[str, Any], source_payload: str) -> None:
    source_rpms = _source_rpm_outputs(spec)
    if not source_rpms:
        print("[build-runner] WARNING: no .src.rpm output declared; skipping source payload verification")
        return

    expected = expected_payload_header(source_payload)
    for path in source_rpms:
        actual = query_source_rpm_payload_header(path)
        print(
            "[build-runner] Source RPM payload for %s: PAYLOADCOMPRESSOR=%s, PAYLOADFLAGS=%s"
            % (path, actual[0], actual[1])
        )
        if expected is None:
            continue
        if actual != expected:
            raise BuildRunnerError(
                "source rpm payload mismatch for '%s': expected PAYLOADCOMPRESSOR=%s, PAYLOADFLAGS=%s; "
                "got PAYLOADCOMPRESSOR=%s, PAYLOADFLAGS=%s"
                % (path, expected[0], expected[1], actual[0], actual[1])
            )


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
    source_payload = setup_default_environment()

    # 2-2. 根据 environment 安装系统包和工具
    handle_environment(spec, os_runner)

    # 3. 执行 phases
    handle_phases(spec)

    # 4. 校验 source rpm payload 策略是否生效
    verify_source_payload_outputs(spec, source_payload)


if __name__ == "__main__":
    try:
        main()
    except BuildRunnerError as exc:
        print(f"[build-runner] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
