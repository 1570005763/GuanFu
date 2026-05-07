import subprocess
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[2]


def run_buildspec_rebuild(args):
    build_runner = _repo_root() / "src" / "build-runner.sh"
    if not build_runner.is_file():
        raise SystemExit(
            "build-runner.sh was not found. "
            "Run this command from a GuanFu source checkout for now."
        )
    return subprocess.call([str(build_runner), args.spec])
