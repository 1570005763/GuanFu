import subprocess
import time
from pathlib import Path

from guanfu.koji_rebuild.downloader import summarize_file


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
    return {
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 1),
        "command": cmd,
        "rpms": [summarize_file(path) for path in _list_result_rpms(resultdir)],
    }
