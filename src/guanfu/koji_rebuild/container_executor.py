import os
import platform
import re
import shutil
import subprocess
from pathlib import Path


DEFAULT_AN23_CONTAINER_IMAGE = "ghcr.io/1570005763/guanfu-koji-rebuild-an23:latest"
CONTAINER_WORKDIR = "/work"


def detect_target_os(rpm_info=None, buildroot=None):
    tag = ((buildroot or {}).get("tag_name") or "").lower()
    release = ((rpm_info or {}).get("release") or "").lower()
    if _has_an23_marker(tag) or _has_an23_marker(release):
        return "an23"
    return None


def is_supported_target_os(target_os):
    return target_os == "an23"


def default_container_image(target_os):
    if target_os == "an23":
        return os.environ.get("GUANFU_AN23_CONTAINER_IMAGE", DEFAULT_AN23_CONTAINER_IMAGE)
    return None


def select_container_runtime(requested="auto"):
    requested = requested or "auto"
    if requested != "auto":
        if shutil.which(requested):
            return requested
        raise RuntimeError(f"container runtime was not found: {requested}")

    order = ["podman", "docker"] if platform.system().lower() == "linux" else ["docker", "podman"]
    for runtime in order:
        if shutil.which(runtime):
            return runtime
    raise RuntimeError("container runtime was not found: tried podman and docker")


def build_container_command(args, runtime, image, host_workdir):
    host_workdir = Path(host_workdir).expanduser().resolve()
    command = [runtime, "run", "--rm"]
    if getattr(args, "container_privileged", True):
        command.append("--privileged")
    command.extend(
        [
            "-v",
            f"{host_workdir}:{CONTAINER_WORKDIR}",
            "-w",
            CONTAINER_WORKDIR,
            "-e",
            "GUANFU_IN_CONTAINER=1",
            "-e",
            "GUANFU_EXECUTOR_MODE=container",
            "-e",
            f"GUANFU_CONTAINER_RUNTIME={runtime}",
            "-e",
            f"GUANFU_CONTAINER_IMAGE={image}",
            "-e",
            f"GUANFU_CONTAINER_PRIVILEGED={str(getattr(args, 'container_privileged', True)).lower()}",
            "-e",
            "GUANFU_TARGET_OS=an23",
            image,
            "guanfu",
            "rebuild",
            "koji-rpm",
            "--rpm-name",
            args.rpm_name,
            "--executor",
            "local",
            "--koji-server",
            args.koji_server,
            "--koji-topurl",
            args.koji_topurl,
            "--binary-rpm-base-url",
            args.binary_rpm_base_url,
            "--source-rpm-base-url",
            args.source_rpm_base_url,
            "--workdir",
            CONTAINER_WORKDIR,
            "--runs",
            str(args.runs),
            "--isolation",
            args.isolation,
            "--repo-fallback",
            args.repo_fallback,
        ]
    )
    return command


def run_container_command(command):
    return subprocess.run(command).returncode


def container_executor_summary(runtime=None, image=None, target_os=None, privileged=None, command=None):
    summary = {
        "mode": "container",
        "target_os": target_os,
        "container_runtime": runtime,
        "container_image": image,
        "privileged": privileged,
        "workdir_in_container": CONTAINER_WORKDIR,
    }
    if command:
        summary["command"] = command
    return _without_none(summary)


def environment_executor_summary(args):
    if os.environ.get("GUANFU_EXECUTOR_MODE") == "container":
        return _without_none(
            {
                "mode": "container",
                "inside_container": os.environ.get("GUANFU_IN_CONTAINER") == "1",
                "target_os": os.environ.get("GUANFU_TARGET_OS"),
                "container_runtime": os.environ.get("GUANFU_CONTAINER_RUNTIME"),
                "container_image": os.environ.get("GUANFU_CONTAINER_IMAGE"),
                "privileged": _parse_bool(os.environ.get("GUANFU_CONTAINER_PRIVILEGED")),
                "workdir_in_container": CONTAINER_WORKDIR,
            }
        )
    return {
        "mode": getattr(args, "executor", "local"),
        "inside_container": os.environ.get("GUANFU_IN_CONTAINER") == "1",
    }


def _has_an23_marker(value):
    if not value:
        return False
    return bool(re.search(r"(^|[^a-z0-9])an23([^a-z0-9]|$)", value))


def _parse_bool(value):
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes", "on")


def _without_none(data):
    return dict((key, value) for key, value in data.items() if value is not None)
