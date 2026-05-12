import subprocess
import time
from pathlib import Path

from guanfu.koji_rebuild.downloader import summarize_file

_LOG_FILES = ("root.log", "build.log", "state.log")
_LOG_TAIL_BYTES = 128 * 1024
_RUNTIME_CRASH_MARKERS = (
    "scriptlet failed, signal 11",
    "scriptlet failed, signal 4",
    "segmentation fault",
    "segfault",
    "illegal instruction",
    "invalid opcode",
)


def _list_result_rpms(resultdir):
    return sorted(Path(resultdir).glob("*.rpm"))


def run_rebuild(mock_cfg, srpm, resultdir, isolation="simple"):
    resultdir = Path(resultdir)
    resultdir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    cmd = [
        "mock",
        "-r",
        str(mock_cfg),
        f"--isolation={isolation}",
        "--resultdir",
        str(resultdir),
        "--rebuild",
        str(srpm),
    ]
    proc = subprocess.run(cmd)
    elapsed = time.time() - started
    result = {
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 1),
        "command": cmd,
        "rpms": [summarize_file(path) for path in _list_result_rpms(resultdir)],
    }
    if proc.returncode != 0:
        diagnosis = _diagnose_mock_failure(resultdir)
        if diagnosis:
            result["failure_diagnosis"] = diagnosis
    return result


def _diagnose_mock_failure(resultdir):
    evidence = _runtime_crash_evidence(resultdir)
    if not evidence:
        return None
    return {
        "category": "buildroot_runtime_incompatible",
        "confidence": 0.85,
        "summary": (
            "mock failed while executing programs or RPM scriptlets inside the buildroot. "
            "This can be caused by old buildroot userland being incompatible with the "
            "current host CPU or kernel execution environment."
        ),
        "suggested_action": (
            "Retry with --executor vm and a Linux VM whose CPU model, kernel, and mock "
            "version are close to the Koji builder."
        ),
        "evidence": evidence,
    }


def _runtime_crash_evidence(resultdir):
    resultdir = Path(resultdir)
    evidence = []
    for log_name in _LOG_FILES:
        log_path = resultdir / log_name
        if not log_path.exists():
            continue
        for line in _read_tail(log_path).splitlines():
            lowered = line.lower()
            if any(marker in lowered for marker in _RUNTIME_CRASH_MARKERS):
                evidence.append({"log": log_name, "line": line.strip()})
                if len(evidence) >= 3:
                    return evidence
    return evidence


def _read_tail(path):
    with Path(path).open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - _LOG_TAIL_BYTES))
        return handle.read().decode(errors="replace")
