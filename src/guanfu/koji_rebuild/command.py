import re
import sys
import time
from pathlib import Path

from guanfu.koji_rebuild.client import KojiClient
from guanfu.koji_rebuild.compare import compare_published_and_rebuilt, compare_srpms
from guanfu.koji_rebuild.downloader import (
    download_task_output,
    join_url,
    summarize_file,
    try_download_url,
)
from guanfu.koji_rebuild.mock_config import generate_mock_config, probe_repodata
from guanfu.koji_rebuild.mock_runner import run_rebuild
from guanfu.koji_rebuild.report import write_json
from guanfu.koji_rebuild.resolver import resolve_koji_build
from guanfu.koji_rebuild.rpm_name import parse_rpm_filename, rpm_filename


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", name)


def _run_dir(workdir, rpm_name):
    base = Path(workdir) / _safe_name(Path(rpm_name).name)
    if not base.exists() or not any(base.iterdir()):
        base.mkdir(parents=True, exist_ok=True)
        return base
    timestamp = time.strftime("run-%Y%m%d%H%M%S")
    path = base / timestamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def _download_koji_logs(client, task_id, outputs, inputs_dir):
    downloads = []
    for log_name in ("build.log", "root.log", "installed_pkgs.log", "mock_output.log"):
        if log_name in outputs:
            path = download_task_output(client, task_id, log_name, inputs_dir / log_name)
            downloads.append(summarize_file(path, label="koji_task_log"))
    return downloads


def _download_task_srpm(client, task_id, srpm_name, inputs_dir):
    path = download_task_output(client, task_id, srpm_name, inputs_dir / f"koji-task-{srpm_name}")
    return path, summarize_file(path, label="koji_task_srpm")


def _download_published(url, dest, label):
    path, error = try_download_url(url, dest)
    if error:
        return None, {"label": label, "url": url, "error": error}
    return path, summarize_file(path, label=label, url=url)


def run_koji_rpm_rebuild(args):
    if args.slsa_provenance:
        print(
            "RPM SLSA provenance input is reserved but not implemented yet. "
            "Please use --rpm-name for Koji RPM rebuild.",
            file=sys.stderr,
        )
        return 2

    if args.runs < 1:
        print("--runs must be >= 1", file=sys.stderr)
        return 2

    run_dir = _run_dir(args.workdir, args.rpm_name)
    inputs_dir = run_dir / "inputs"
    results_dir = run_dir / "results"
    metadata_dir = run_dir / "metadata"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "input": {
            "rpm_name": args.rpm_name,
            "koji_server": args.koji_server,
            "koji_topurl": args.koji_topurl,
            "binary_rpm_base_url": args.binary_rpm_base_url,
            "source_rpm_base_url": args.source_rpm_base_url,
            "runs": args.runs,
            "isolation": args.isolation,
        },
        "workdir": str(run_dir),
    }

    try:
        rpm_info = parse_rpm_filename(args.rpm_name)
        client = KojiClient(args.koji_server)
        resolution = resolve_koji_build(client, rpm_info)
        target_rpm_name = rpm_filename(resolution.rpm)

        write_json(metadata_dir / "rpm.json", resolution.rpm)
        write_json(metadata_dir / "build.json", resolution.build)
        write_json(metadata_dir / "buildroot.json", resolution.buildroot)
        write_json(metadata_dir / "buildarch-task.json", resolution.buildarch_task)
        write_json(metadata_dir / "task-result.json", resolution.task_result)

        repo_probe = probe_repodata(args.koji_topurl, resolution.buildroot)
        report["repo_probe"] = repo_probe
        write_json(metadata_dir / "repo-probe.json", repo_probe)
        if repo_probe.get("status") != 200:
            report["status"] = "skipped"
            report["reason"] = "original buildroot repo repomd.xml is not available"
            write_json(run_dir / "report.json", report)
            print(f"[guanfu] Historical repo is not available: {repo_probe.get('url')}", file=sys.stderr)
            return 3

        published_rpm_url = join_url(args.binary_rpm_base_url, target_rpm_name)
        published_rpm, published_rpm_summary = _download_published(
            published_rpm_url,
            inputs_dir / target_rpm_name,
            "published_rpm",
        )
        if not published_rpm:
            raise RuntimeError(f"failed to download published RPM: {published_rpm_summary}")

        published_srpm_url = join_url(args.source_rpm_base_url, resolution.task_srpm_name)
        published_srpm, published_srpm_summary = _download_published(
            published_srpm_url,
            inputs_dir / resolution.task_srpm_name,
            "published_srpm",
        )

        task_srpm, task_srpm_summary = _download_task_srpm(
            client,
            resolution.buildarch_task["id"],
            resolution.task_srpm_name,
            inputs_dir,
        )
        log_summaries = _download_koji_logs(
            client,
            resolution.buildarch_task["id"],
            resolution.outputs,
            inputs_dir,
        )

        downloads = [published_rpm_summary, task_srpm_summary] + log_summaries
        if published_srpm_summary:
            downloads.insert(1, published_srpm_summary)
        report["downloads"] = downloads

        srpm_for_rebuild = published_srpm
        report["srpm_source"] = "openanolis_source_mirror"
        report["srpm_is_published_artifact"] = True
        if not srpm_for_rebuild:
            srpm_for_rebuild = task_srpm
            report["srpm_source"] = "koji_task_fallback"
            report["srpm_is_published_artifact"] = False

        report["srpm_cross_check"] = compare_srpms(published_srpm, task_srpm)

        mock_cfg = inputs_dir / "mock.cfg"
        generate_mock_config(
            args.koji_server,
            args.koji_topurl,
            resolution.buildroot["id"],
            mock_cfg,
        )
        report["mock_config"] = {
            "file": str(mock_cfg),
            "buildroot_id": resolution.buildroot["id"],
        }

        rebuilds = []
        for run_index in range(1, args.runs + 1):
            resultdir = results_dir / f"result-run-{run_index}"
            result = run_rebuild(mock_cfg, srpm_for_rebuild, resultdir, isolation=args.isolation)
            result["run"] = run_index
            rebuilds.append(result)
            if result["exit_code"] != 0:
                break
        report["rebuilds"] = rebuilds

        successful = rebuilds and all(item["exit_code"] == 0 for item in rebuilds)
        report["status"] = "rebuilt" if successful else "failed"
        if successful:
            first_run_rpms = [Path(item.get("path", item["file"])) for item in rebuilds[0]["rpms"]]
            report["comparison"] = compare_published_and_rebuilt(
                published_rpm,
                first_run_rpms,
                target_rpm_name,
            )
            if len(rebuilds) > 1:
                first = [(rpm["file"], rpm["sha256"]) for rpm in rebuilds[0]["rpms"]]
                report["repeatable_by_rpm_sha256"] = all(
                    [(rpm["file"], rpm["sha256"]) for rpm in rebuild["rpms"]] == first
                    for rebuild in rebuilds[1:]
                )

        write_json(run_dir / "report.json", report)
        print(f"[guanfu] Koji RPM rebuild report: {run_dir / 'report.json'}")
        return 0 if successful else 1
    except Exception as exc:
        report["status"] = "error"
        report["error"] = repr(exc)
        write_json(run_dir / "report.json", report)
        print(f"[guanfu] ERROR: {exc}", file=sys.stderr)
        print(f"[guanfu] Partial report: {run_dir / 'report.json'}", file=sys.stderr)
        return 1
