import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from guanfu.koji_rebuild.downloader import download_url, summarize_file
from guanfu.koji_rebuild.mock_runner import _diagnose_mock_failure


DEFAULT_VM_WORKDIR = "/mnt/guanfu-work"
DEFAULT_MOUNT_TAG = "guanfu_work"
DEFAULT_AN23_VM_IMAGE_BASE_URL = "https://mirrors.openanolis.cn/anolis/23/isos/GA/x86_64/"
DEFAULT_AN23_VM_IMAGE_FILENAME = "AnolisOS-23.4-x86_64.qcow2"
DEFAULT_AN23_VM_IMAGE_URL = DEFAULT_AN23_VM_IMAGE_BASE_URL + DEFAULT_AN23_VM_IMAGE_FILENAME
AN23_VM_PROFILE = {
    "name": "an23-koji-cascadelake",
    "target_os": "an23",
    "default_image_url": DEFAULT_AN23_VM_IMAGE_URL,
    "default_image_format": "qcow2",
    "qemu_cpu": "Cascadelake-Server-v1",
    "expected_cpu_model": "Intel(R) Xeon(R) Platinum 8269CY CPU T 3.10GHz",
    "expected_cpu_family": "6",
    "expected_cpu_model_id": "85",
    "expected_kernel": "4.18.0-193.28.1.el8_2.x86_64",
    "expected_mock": "mock-2.12-1.el8",
}


def detect_target_os(rpm_info=None, buildroot=None):
    tag = ((buildroot or {}).get("tag_name") or "").lower()
    release = ((rpm_info or {}).get("release") or "").lower()
    if _has_an23_marker(tag) or _has_an23_marker(release):
        return "an23"
    return None


def is_supported_target_os(target_os):
    return target_os == "an23"


def parse_koji_recorded_environment(resolution, inputs_dir):
    buildroot = resolution.buildroot if resolution else {}
    env = {
        "host_id": buildroot.get("host_id"),
        "host_name": buildroot.get("host_name"),
        "container_type": buildroot.get("container_type"),
    }
    hw_info = _read_text(Path(inputs_dir) / "hw_info.log")
    if hw_info:
        env.update(
            _without_none(
                {
                    "cpu_model": _first_match(hw_info, r"(?m)^Model name:\s*(.+)$"),
                    "cpu_family": _first_match(hw_info, r"(?m)^CPU family:\s*(.+)$"),
                    "cpu_model_id": _first_match(hw_info, r"(?m)^Model:\s*(.+)$"),
                    "cpu_stepping": _first_match(hw_info, r"(?m)^Stepping:\s*(.+)$"),
                    "hypervisor_vendor": _first_match(hw_info, r"(?m)^Hypervisor vendor:\s*(.+)$"),
                    "virtualization_type": _first_match(hw_info, r"(?m)^Virtualization type:\s*(.+)$"),
                    "cpu_flags": _split_flags(_first_match(hw_info, r"(?m)^Flags:\s*(.+)$")),
                }
            )
        )
    root_log = _read_text(Path(inputs_dir) / "root.log")
    if root_log:
        env["kernel"] = _first_match(root_log, r"kernel version ==\s*([^\s]+)")
    mock_output = _read_text(Path(inputs_dir) / "mock_output.log") or root_log
    if mock_output:
        env["mock"] = _first_match(mock_output, r"NVR\s*=\s*([^)]+)")
        if not env.get("mock"):
            version = _first_match(mock_output, r"mock\.py version\s+([^\s]+)")
            if version:
                env["mock"] = "mock-%s" % version
    return _without_none(env)


def prepare_vm_mock_config(source_cfg, dest_cfg, run_dir, vm_workdir=DEFAULT_VM_WORKDIR):
    source_cfg = Path(source_cfg)
    dest_cfg = Path(dest_cfg)
    run_dir = str(Path(run_dir).resolve())
    text = source_cfg.read_text()
    text = text.replace(run_dir, vm_workdir.rstrip("/"))
    dest_cfg.write_text(text)
    return dest_cfg


def run_vm_rebuild(args, run_dir, mock_cfg, srpm, results_dir, target_os, koji_recorded=None):
    run_dir = Path(run_dir).resolve()
    mock_cfg = Path(mock_cfg).resolve()
    srpm = Path(srpm).resolve()
    results_dir = Path(results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    profile = _select_vm_profile(target_os, args)
    qemu_binary = _select_qemu_binary(getattr(args, "vm_qemu_binary", None))
    acceleration, acceleration_warning = _select_acceleration(getattr(args, "vm_require_kvm", False))
    preflight = _preflight_vm_host_dependencies(args, profile, qemu_binary, acceleration_warning)
    share_mode = _select_share_mode(args, qemu_binary)
    if share_mode == "image-copy":
        preflight["checks"].extend(_preflight_image_copy_dependencies(args))
    preflight["checks"].append({"name": "vm-share-mode", "status": "ok", "value": share_mode})
    vm_image = prepare_vm_image(args, run_dir, profile)
    boot_mode = _select_boot_mode(args, vm_image)
    transfer = {"mode": share_mode}

    vm_mock_cfg = Path(mock_cfg).parent / "mock-vm.cfg"
    prepare_vm_mock_config(mock_cfg, vm_mock_cfg, run_dir, getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR))

    script = _vm_rebuild_script(
        vm_workdir=getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR),
        mock_cfg=Path(getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR)) / vm_mock_cfg.relative_to(run_dir),
        srpm=Path(getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR)) / Path(srpm).resolve().relative_to(run_dir),
        results_dir=Path(getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR)) / results_dir.resolve().relative_to(run_dir),
        runs=args.runs,
        isolation=args.isolation,
        mount_workdir=share_mode == "9p",
    )
    if boot_mode == "direct-init":
        _inject_vm_script_raw(vm_image["path"], run_dir, script)
    else:
        _inject_vm_script_systemd(vm_image["path"], run_dir, script, args)
    if share_mode == "image-copy":
        transfer["copy_in"] = _copy_inputs_into_vm(vm_image["path"], run_dir, args)

    command = build_qemu_command(
        args,
        qemu_binary=qemu_binary,
        profile=profile,
        acceleration=acceleration,
        shared_dir=run_dir,
        vm_image=vm_image,
        boot_mode=boot_mode,
        share_mode=share_mode,
    )
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(command, timeout=getattr(args, "vm_timeout", 7200))
        qemu_exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        qemu_exit_code = 124
    elapsed = time.time() - started

    if share_mode == "image-copy":
        try:
            transfer["copy_out"] = _copy_outputs_from_vm(vm_image["path"], run_dir, args)
        except Exception as exc:
            transfer["copy_out_error"] = repr(exc)

    vm_log = run_dir / "metadata" / "vm-rebuild.log"
    actual = parse_vm_actual_environment(vm_log)
    executor = vm_executor_summary(
        profile=profile,
        acceleration=acceleration,
        qemu_binary=qemu_binary,
        command=command,
        elapsed_seconds=round(elapsed, 1),
        qemu_exit_code=qemu_exit_code,
        timed_out=timed_out,
        koji_recorded=koji_recorded,
        actual_vm=actual,
        vm_image=vm_image,
        boot_mode=boot_mode,
        share_mode=share_mode,
        transfer=transfer,
        preflight=preflight,
    )
    rebuilds = _collect_vm_rebuilds(args, vm_mock_cfg, srpm, results_dir, qemu_exit_code, elapsed)
    return {
        "executor": executor,
        "rebuilds": rebuilds,
        "qemu_exit_code": qemu_exit_code,
        "vm_log": str(vm_log),
    }


def build_qemu_command(
    args,
    qemu_binary,
    profile,
    acceleration,
    shared_dir,
    vm_image=None,
    boot_mode=None,
    share_mode="9p",
):
    vm_image = vm_image or {
        "path": getattr(args, "vm_image", None),
        "format": getattr(args, "vm_image_format", "raw"),
    }
    boot_mode = boot_mode or _select_boot_mode(args, vm_image)
    command = [
        qemu_binary,
        "-accel",
        acceleration,
        "-machine",
        getattr(args, "vm_machine", "q35"),
        "-cpu",
        profile["qemu_cpu"],
        "-smp",
        str(getattr(args, "vm_smp", 2)),
        "-m",
        str(getattr(args, "vm_memory", "4096M")),
        "-nographic",
        "-no-reboot",
        "-nic",
        "user,model=virtio-net-pci",
        "-drive",
        "file=%s,if=virtio,format=%s" % (vm_image["path"], vm_image["format"]),
    ]
    if share_mode == "9p":
        command.extend(
            [
                "-virtfs",
                (
                    "local,path=%s,mount_tag=%s,security_model=none,id=%s"
                    % (Path(shared_dir).resolve(), DEFAULT_MOUNT_TAG, DEFAULT_MOUNT_TAG)
                ),
            ]
        )
    if boot_mode == "direct-init":
        command.extend(
            [
                "-kernel",
                args.vm_kernel,
                "-initrd",
                args.vm_initrd,
                "-append",
                (
                    "console=ttyS0 root=%s rw init=/root/guanfu-vm-rebuild.sh "
                    "selinux=0 enforcing=0 panic=1"
                )
                % getattr(args, "vm_root_device", "/dev/vda"),
            ]
        )
    return command


def vm_executor_summary(
    profile=None,
    acceleration=None,
    qemu_binary=None,
    command=None,
    elapsed_seconds=None,
    qemu_exit_code=None,
    timed_out=False,
    koji_recorded=None,
    actual_vm=None,
    target_os=None,
    vm_image=None,
    boot_mode=None,
    share_mode=None,
    transfer=None,
    preflight=None,
):
    profile = profile or {}
    koji_recorded = koji_recorded or {}
    actual_vm = actual_vm or {}
    summary = {
        "mode": "vm",
        "target_os": target_os or profile.get("target_os"),
        "vm_profile": profile.get("name"),
        "acceleration": acceleration,
        "qemu_binary": qemu_binary,
        "qemu_cpu": profile.get("qemu_cpu"),
        "vm_image": _vm_image_summary(vm_image),
        "boot_mode": boot_mode,
        "share_mode": share_mode,
        "transfer": transfer,
        "preflight": preflight,
        "qemu_exit_code": qemu_exit_code,
        "qemu_elapsed_seconds": elapsed_seconds,
        "timed_out": timed_out or None,
        "koji_recorded": koji_recorded,
        "actual_vm": actual_vm,
        "environment_match": _environment_match(koji_recorded, actual_vm, profile),
    }
    summary["trust_environment"] = _trust_environment(summary)
    if command:
        summary["command"] = command
    return _without_none(summary)


def parse_vm_actual_environment(log_path):
    data = {}
    for line in (_read_text(log_path) or "").splitlines():
        if not line.startswith("VM_ACTUAL_") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key[len("VM_ACTUAL_") :].lower()] = value.strip()
    flags = data.get("cpu_flags")
    if flags:
        data["cpu_flags"] = _split_flags(flags)
    return _without_none(data)


def _collect_vm_rebuilds(args, mock_cfg, srpm, results_dir, qemu_exit_code, qemu_elapsed):
    rebuilds = []
    for run_index in range(1, args.runs + 1):
        resultdir = Path(results_dir) / ("result-run-%s" % run_index)
        exit_file = resultdir / "mock.exit"
        if exit_file.exists():
            exit_code = _read_int(exit_file, default=1)
        elif run_index == 1:
            exit_code = qemu_exit_code
        else:
            break
        result = {
            "run": run_index,
            "exit_code": exit_code,
            "elapsed_seconds": None if exit_file.exists() else round(qemu_elapsed, 1),
            "command": _mock_command(mock_cfg, srpm, resultdir, args.isolation),
            "rpms": [summarize_file(path) for path in sorted(resultdir.glob("*.rpm"))],
        }
        if exit_code != 0:
            diagnosis = _diagnose_mock_failure(resultdir)
            if diagnosis:
                result["failure_diagnosis"] = diagnosis
        rebuilds.append(result)
        if exit_code != 0:
            break
    return rebuilds


def _mock_command(mock_cfg, srpm, resultdir, isolation):
    return [
        "mock",
        "-r",
        str(mock_cfg),
        "--isolation=%s" % isolation,
        "--resultdir",
        str(resultdir),
        "--rebuild",
        str(srpm),
    ]


def _vm_rebuild_script(vm_workdir, mock_cfg, srpm, results_dir, runs, isolation, mount_workdir=True):
    mount_setup = ""
    if mount_workdir:
        mount_setup = """if ! mount -t 9p -o trans=virtio,version=9p2000.L,msize=104857600 "{mount_tag}" "{vm_workdir}"; then
  echo "VM_MOUNT_FAILED"
  sync
  poweroff -f || reboot -f || halt -f
fi
""".format(
            mount_tag=DEFAULT_MOUNT_TAG,
            vm_workdir=vm_workdir,
        )
    return """#!/bin/bash
exec >/root/guanfu-vm-bootstrap.log 2>&1
set -x
setenforce 0 || true
mount -t proc proc /proc || true
mount -t sysfs sysfs /sys || true
mount -t devtmpfs devtmpfs /dev || true
mount -t tmpfs tmpfs /run || true
mkdir -p /run/lock
mkdir -p /etc
if [ ! -s /etc/resolv.conf ]; then
  rm -f /etc/resolv.conf
  printf "# GUANFU_VM_RESOLV_FIX\\nnameserver 223.5.5.5\\n" > /etc/resolv.conf
fi
mkdir -p "{vm_workdir}"
{mount_setup}
mkdir -p "{vm_workdir}/metadata" "{results_dir}"
exec >"{vm_workdir}/metadata/vm-rebuild.log" 2>&1
cat /root/guanfu-vm-bootstrap.log || true
echo "VM_BATCH_START $(date -Is)"
if ! command -v mock >/dev/null 2>&1; then
  dnf -y install mock rpm-build || yum -y install mock rpm-build || true
fi
echo "VM_ACTUAL_KERNEL=$(uname -r)"
echo "VM_ACTUAL_MOCK=$(mock --version 2>/dev/null | head -1)"
echo "VM_ACTUAL_RPM=$(rpm --version 2>/dev/null | head -1)"
echo "VM_ACTUAL_DNF=$(dnf --version 2>/dev/null | head -1)"
echo "VM_ACTUAL_CPU_MODEL=$(lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1)"
echo "VM_ACTUAL_CPU_FAMILY=$(lscpu 2>/dev/null | sed -n 's/^CPU family:[[:space:]]*//p' | head -1)"
echo "VM_ACTUAL_CPU_MODEL_ID=$(lscpu 2>/dev/null | sed -n 's/^Model:[[:space:]]*//p' | head -1)"
echo "VM_ACTUAL_CPU_FLAGS=$(grep -m1 '^flags' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')"
run=1
while [ "$run" -le "{runs}" ]; do
  resultdir="{results_dir}/result-run-$run"
  rm -rf "$resultdir"
  mkdir -p "$resultdir"
  echo "VM_REBUILD_PKG_RUN_START $run $(date -Is)"
  mock -r "{mock_cfg}" --isolation="{isolation}" --resultdir "$resultdir" --rebuild "{srpm}"
  rc=$?
  echo "$rc" > "$resultdir/mock.exit"
  echo "VM_REBUILD_PKG_RUN_EXIT $run $rc $(date -Is)"
  find "$resultdir" -maxdepth 1 -type f -printf "%f %s bytes\\n" | sort || true
  if [ "$rc" -ne 0 ]; then
    break
  fi
  run=$((run + 1))
done
echo "VM_BATCH_END $(date -Is)"
sync
poweroff -f || reboot -f || halt -f
""".format(
        vm_workdir=vm_workdir,
        mount_setup=mount_setup,
        mock_cfg=mock_cfg,
        srpm=srpm,
        results_dir=results_dir,
        runs=runs,
        isolation=isolation,
    )


def prepare_vm_image(args, run_dir, profile):
    image_ref = getattr(args, "vm_image", None) or profile.get("default_image_url")
    if not image_ref:
        raise RuntimeError("VM image is required for --executor vm")

    image_format_hint = _resolve_image_format_from_ref(args, image_ref, profile)
    if image_format_hint == "qcow2":
        _select_qemu_img_binary(getattr(args, "vm_qemu_img_binary", None))

    base_image, source = _resolve_vm_image(image_ref, run_dir)
    image_format = _resolve_image_format(args, base_image, profile)
    if image_format == "raw":
        _validate_direct_boot_paths(args)
        return {
            "path": str(base_image),
            "format": "raw",
            "source": source,
        }

    if image_format != "qcow2":
        raise RuntimeError("unsupported VM image format: %s" % image_format)

    qemu_img = _select_qemu_img_binary(getattr(args, "vm_qemu_img_binary", None))
    overlay = Path(run_dir) / "metadata" / "vm-overlay.qcow2"
    if overlay.exists():
        overlay.unlink()
    subprocess.run(
        [
            qemu_img,
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(base_image),
            str(overlay),
        ],
        check=True,
    )
    return {
        "path": str(overlay),
        "format": "qcow2",
        "source": source,
        "overlay": str(overlay),
        "qemu_img": qemu_img,
    }


def _resolve_vm_image(image_ref, run_dir):
    if _is_url(image_ref):
        filename = Path(urllib.parse.urlparse(image_ref).path).name
        if not filename:
            raise RuntimeError("VM image URL does not include a filename: %s" % image_ref)
        cache_dir = Path(run_dir).parent / "vm-cache"
        cached = cache_dir / filename
        if not cached.exists() or cached.stat().st_size == 0:
            download_url(image_ref, cached)
        return cached.resolve(), summarize_file(cached, label="vm_image", url=image_ref)

    image = Path(image_ref).expanduser()
    if not image.exists():
        raise RuntimeError("VM image was not found: %s" % image_ref)
    return image.resolve(), summarize_file(image, label="vm_image")


def _resolve_image_format(args, image_path, profile):
    requested = getattr(args, "vm_image_format", "auto")
    if requested and requested != "auto":
        return requested
    suffix = Path(image_path).suffix.lower()
    if suffix in (".qcow2", ".qcow"):
        return "qcow2"
    if suffix in (".raw", ".img"):
        return "raw"
    return profile.get("default_image_format") or "qcow2"


def _resolve_image_format_from_ref(args, image_ref, profile):
    requested = getattr(args, "vm_image_format", "auto")
    if requested and requested != "auto":
        return requested
    suffix = Path(urllib.parse.urlparse(str(image_ref)).path).suffix.lower()
    if suffix in (".qcow2", ".qcow"):
        return "qcow2"
    if suffix in (".raw", ".img"):
        return "raw"
    return profile.get("default_image_format") or "qcow2"


def _select_boot_mode(args, vm_image):
    if vm_image.get("format") == "raw" and getattr(args, "vm_kernel", None) and getattr(args, "vm_initrd", None):
        return "direct-init"
    return "systemd"


def _select_qemu_img_binary(requested=None):
    if requested:
        if Path(requested).exists() or shutil.which(requested):
            return requested
        raise RuntimeError("qemu-img binary was not found: %s" % requested)
    resolved = shutil.which("qemu-img")
    if resolved:
        return resolved
    raise RuntimeError(
        "qemu-img binary was not found. Please install qemu-img/qemu-utils "
        "(for example: dnf install qemu-img, or apt install qemu-utils)."
    )


def _preflight_vm_host_dependencies(args, profile, qemu_binary, acceleration_warning=None):
    image_ref = getattr(args, "vm_image", None) or profile.get("default_image_url")
    image_format = _resolve_image_format_from_ref(args, image_ref, profile)
    checks = [
        {"name": "qemu", "status": "ok", "path": qemu_binary},
        {"name": "acceleration", "status": "ok", "value": "tcg" if acceleration_warning else "kvm"},
    ]
    warnings = []
    if acceleration_warning:
        warnings.append(acceleration_warning)
        print("[guanfu] WARNING: %s" % acceleration_warning, file=sys.stderr)
    if image_format == "qcow2":
        checks.append(
            {
                "name": "qemu-img",
                "status": "ok",
                "path": _select_qemu_img_binary(getattr(args, "vm_qemu_img_binary", None)),
            }
        )
        checks.append(
            {
                "name": "virt-customize",
                "status": "ok",
                "path": _select_virt_customize_binary(getattr(args, "vm_virt_customize_binary", None)),
            }
        )
    return _without_none({"checks": checks, "warnings": warnings or None})


def _preflight_image_copy_dependencies(args):
    return [
        {
            "name": "virt-copy-in",
            "status": "ok",
            "path": _select_virt_copy_binary("virt-copy-in", getattr(args, "vm_virt_copy_in_binary", None)),
        },
        {
            "name": "virt-copy-out",
            "status": "ok",
            "path": _select_virt_copy_binary("virt-copy-out", getattr(args, "vm_virt_copy_out_binary", None)),
        },
    ]


def _select_share_mode(args, qemu_binary):
    requested = getattr(args, "vm_share_mode", "auto")
    if requested in ("9p", "image-copy"):
        return requested
    if requested != "auto":
        raise RuntimeError("unsupported VM share mode: %s" % requested)
    return "9p" if _qemu_supports_9p(qemu_binary) else "image-copy"


def _qemu_supports_9p(qemu_binary):
    try:
        proc = subprocess.run(
            [qemu_binary, "-device", "help"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    return "virtio-9p-pci" in proc.stdout


def _copy_inputs_into_vm(image, run_dir, args):
    virt_copy_in = _select_virt_copy_binary("virt-copy-in", getattr(args, "vm_virt_copy_in_binary", None))
    paths = [Path(run_dir) / "inputs"]
    fallback_repo = Path(run_dir) / "fallback-repo"
    if fallback_repo.exists():
        paths.append(fallback_repo)
    subprocess.run(
        [virt_copy_in, "-a", str(image)] + [str(path) for path in paths] + [getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR)],
        check=True,
        env=_libguestfs_env(),
    )
    return {
        "tool": virt_copy_in,
        "paths": [str(path) for path in paths],
        "destination": getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR),
    }


def _copy_outputs_from_vm(image, run_dir, args):
    virt_copy_out = _select_virt_copy_binary("virt-copy-out", getattr(args, "vm_virt_copy_out_binary", None))
    subprocess.run(
        [
            virt_copy_out,
            "-a",
            str(image),
            str(Path(getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR)) / "results"),
            str(Path(getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR)) / "metadata"),
            str(run_dir),
        ],
        check=True,
        env=_libguestfs_env(),
    )
    return {
        "tool": virt_copy_out,
        "paths": ["results", "metadata"],
        "destination": str(run_dir),
    }


def _select_virt_copy_binary(default, requested=None):
    if requested:
        if Path(requested).exists() or shutil.which(requested):
            return requested
        raise RuntimeError("%s binary was not found: %s" % (default, requested))
    resolved = shutil.which(default)
    if resolved:
        return resolved
    raise RuntimeError(
        "%s is required for image-copy VM share mode. Please install libguestfs tools "
        "(for example: dnf install libguestfs-tools-c, or apt install libguestfs-tools)."
        % default
    )


def _inject_vm_script_raw(image, run_dir, script):
    if getattr(_inject_vm_script_raw, "_skip_for_tests", False):
        return
    mount_dir = Path(run_dir) / "metadata" / "vm-image-mount"
    mount_dir.mkdir(parents=True, exist_ok=True)
    mounted = False
    try:
        subprocess.run(["mount", "-o", "loop", str(image), str(mount_dir)], check=True)
        mounted = True
        script_path = mount_dir / "root" / "guanfu-vm-rebuild.sh"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script)
        script_path.chmod(0o755)
    finally:
        if mounted:
            subprocess.run(["umount", str(mount_dir)], check=False)


def _inject_vm_script_systemd(image, run_dir, script, args):
    if getattr(_inject_vm_script_systemd, "_skip_for_tests", False):
        return
    virt_customize = _select_virt_customize_binary(getattr(args, "vm_virt_customize_binary", None))
    metadata_dir = Path(run_dir) / "metadata"
    script_path = metadata_dir / "guanfu-vm-rebuild.sh"
    service_path = metadata_dir / "guanfu-vm-rebuild.service"
    script_path.write_text(script)
    script_path.chmod(0o755)
    service_path.write_text(_systemd_service())
    command = [
        virt_customize,
        "-a",
        str(image),
    ]
    prepare_packages = getattr(args, "vm_prepare_packages", "mock,rpm-build")
    if prepare_packages:
        command.extend(["--run-command", _guest_prepare_package_command(prepare_packages)])
    command.extend(
        [
            "--upload",
            "%s:/root/guanfu-vm-rebuild.sh" % script_path,
            "--upload",
            "%s:/etc/systemd/system/guanfu-vm-rebuild.service" % service_path,
            "--run-command",
            "mkdir -p %s" % getattr(args, "vm_workdir", DEFAULT_VM_WORKDIR),
            "--run-command",
            "chmod 755 /root/guanfu-vm-rebuild.sh",
            "--run-command",
            "mkdir -p /etc/systemd/system/multi-user.target.wants",
            "--run-command",
            (
                "ln -sf /etc/systemd/system/guanfu-vm-rebuild.service "
                "/etc/systemd/system/multi-user.target.wants/guanfu-vm-rebuild.service"
            ),
        ]
    )
    subprocess.run(
        command,
        check=True,
        env=_libguestfs_env(),
    )


def _select_virt_customize_binary(requested=None):
    if requested:
        if Path(requested).exists() or shutil.which(requested):
            return requested
        raise RuntimeError("virt-customize binary was not found: %s" % requested)
    resolved = shutil.which("virt-customize")
    if resolved:
        return resolved
    raise RuntimeError(
        "virt-customize is required to prepare qcow2 VM images. Please install libguestfs tools "
        "(for example: dnf install libguestfs-tools-c, or apt install libguestfs-tools)."
    )


def _libguestfs_env():
    env = os.environ.copy()
    env.setdefault("LIBGUESTFS_BACKEND", "direct")
    return env


def _guest_prepare_package_command(packages):
    package_args = " ".join(
        shlex.quote(item) for item in re.split(r"[,\s]+", packages or "") if item
    )
    return (
        "mkdir -p /etc && "
        "rm -f /etc/resolv.conf && "
        "printf '# GUANFU_VM_RESOLV_FIX\\nnameserver 223.5.5.5\\n' > /etc/resolv.conf && "
        "if command -v dnf >/dev/null 2>&1; then dnf -y install {packages}; "
        "elif command -v yum >/dev/null 2>&1; then yum -y install {packages}; "
        "else true; fi"
    ).format(packages=package_args)


def _systemd_service():
    return """[Unit]
Description=GuanFu Koji RPM rebuild
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/root/guanfu-vm-rebuild.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def _select_vm_profile(target_os, args):
    if target_os != "an23":
        raise RuntimeError("only an23 Koji RPM rebuild is currently supported by the VM executor")
    profile = dict(AN23_VM_PROFILE)
    if getattr(args, "vm_cpu", None):
        profile["qemu_cpu"] = args.vm_cpu
    return profile


def _select_qemu_binary(requested=None):
    if requested:
        if Path(requested).exists() or shutil.which(requested):
            return requested
        raise RuntimeError("QEMU binary was not found: %s" % requested)
    for candidate in ("qemu-system-x86_64", "qemu-kvm", "/usr/libexec/qemu-kvm"):
        resolved = shutil.which(candidate) if not candidate.startswith("/") else candidate
        if resolved and Path(resolved).exists():
            return resolved
    raise RuntimeError(
        "QEMU binary was not found. Please install qemu-system-x86_64 or qemu-kvm "
        "(for example: dnf install qemu-kvm, or apt install qemu-system-x86)."
    )


def _select_acceleration(require_kvm=False):
    if _kvm_available():
        return "kvm", None
    if require_kvm:
        raise RuntimeError(
            "/dev/kvm is not available. KVM is required because --vm-require-kvm was set. "
            "Run on a host with hardware virtualization exposed, or remove --vm-require-kvm "
            "to allow slow degraded TCG execution."
        )
    return (
        "tcg",
        "/dev/kvm is not available; falling back to slow QEMU TCG. "
        "The rebuild can be used as proof-of-flow, but the executor environment is degraded.",
    )


def _kvm_available():
    return Path("/dev/kvm").exists()


def _validate_direct_boot_paths(args):
    for attr, label in (("vm_kernel", "VM kernel"), ("vm_initrd", "VM initrd")):
        value = getattr(args, attr, None)
        if not value or not Path(value).exists():
            raise RuntimeError("%s was not found: %s" % (label, value))


def _environment_match(koji_recorded, actual_vm, profile):
    if not actual_vm:
        return None
    return _without_none(
        {
            "cpu": _cpu_match(koji_recorded, actual_vm, profile),
            "kernel": _value_match(koji_recorded.get("kernel") or profile.get("expected_kernel"), actual_vm.get("kernel")),
            "mock": _mock_match(koji_recorded.get("mock") or profile.get("expected_mock"), actual_vm.get("mock")),
        }
    )


def _trust_environment(summary):
    matches = summary.get("environment_match") or {}
    if summary.get("acceleration") != "kvm":
        return "degraded"
    if matches.get("kernel") == "exact" and matches.get("mock") == "exact" and matches.get("cpu") in ("exact", "partial"):
        return "trusted"
    return "degraded"


def _cpu_match(koji_recorded, actual_vm, profile):
    expected_model = koji_recorded.get("cpu_model") or profile.get("expected_cpu_model")
    actual_model = actual_vm.get("cpu_model")
    if expected_model and actual_model and expected_model == actual_model:
        return "exact"
    expected_family = koji_recorded.get("cpu_family") or profile.get("expected_cpu_family")
    expected_model_id = koji_recorded.get("cpu_model_id") or profile.get("expected_cpu_model_id")
    if (
        expected_family
        and expected_model_id
        and actual_vm.get("cpu_family") == str(expected_family)
        and actual_vm.get("cpu_model_id") == str(expected_model_id)
    ):
        return "partial"
    if actual_model:
        return "mismatch"
    return None


def _vm_image_summary(vm_image):
    if not vm_image:
        return None
    return _without_none(
        {
            "format": vm_image.get("format"),
            "path": vm_image.get("path"),
            "source": vm_image.get("source"),
            "overlay": vm_image.get("overlay"),
            "qemu_img": vm_image.get("qemu_img"),
        }
    )


def _value_match(expected, actual):
    if not expected or not actual:
        return None
    return "exact" if str(expected) == str(actual) else "mismatch"


def _mock_match(expected, actual):
    if not expected or not actual:
        return None
    if str(expected) == str(actual) or str(expected) in str(actual):
        return "exact"
    expected_version = _first_match(str(expected), r"mock-([0-9.]+)")
    if expected_version and expected_version in str(actual):
        return "exact"
    return "mismatch"


def _read_text(path):
    path = Path(path)
    if not path.exists():
        return None
    return path.read_text(errors="replace")


def _read_int(path, default):
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return default


def _first_match(text, pattern):
    match = re.search(pattern, text or "")
    return match.group(1).strip() if match else None


def _split_flags(value):
    if not value:
        return None
    return [item for item in value.split() if item]


def _has_an23_marker(value):
    if not value:
        return False
    return bool(re.search(r"(^|[^a-z0-9])an23([^a-z0-9]|$)", value))


def _is_url(value):
    parsed = urllib.parse.urlparse(str(value))
    return parsed.scheme in ("http", "https")


def _without_none(data):
    return dict((key, value) for key, value in data.items() if value is not None)
