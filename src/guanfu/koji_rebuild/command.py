import re
import sys
import time
from datetime import datetime
from pathlib import Path

from guanfu.koji_rebuild.assessment import ASSESSMENT_VERSION
from guanfu.koji_rebuild.client import KojiClient
from guanfu.koji_rebuild.compare import compare_published_and_rebuilt, compare_srpms
from guanfu.koji_rebuild.vm_executor import (
    detect_target_os,
    is_supported_target_os,
    parse_koji_recorded_environment,
    run_vm_rebuild,
    vm_executor_summary,
)
from guanfu.koji_rebuild.downloader import (
    download_task_output,
    join_url,
    summarize_file,
    try_download_url,
)
from guanfu.koji_rebuild.mock_config import generate_mock_config, probe_repodata
from guanfu.koji_rebuild.mock_runner import run_rebuild
from guanfu.koji_rebuild.report import write_json
from guanfu.koji_rebuild.repo_fallback import (
    prepare_installed_pkgs_fallback,
    summarize_fallback_report,
)
from guanfu.koji_rebuild.resolver import resolve_koji_build
from guanfu.koji_rebuild.rpm_name import parse_rpm_filename, rpm_filename


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", name)


def _analysis_time():
    return datetime.now().astimezone().isoformat()


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
    for log_name in ("build.log", "root.log", "installed_pkgs.log", "mock_output.log", "hw_info.log", "state.log"):
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


def _without_none(data):
    return dict((key, value) for key, value in data.items() if value is not None)


def _public_artifact(summary, source_type=None, url=None, task_id=None):
    if not summary:
        return None
    artifact = {}
    for key in ("file", "url", "size", "sha256", "error"):
        if key in summary:
            artifact[key] = summary[key]
    if url:
        artifact["url"] = url
    if source_type:
        artifact["source_type"] = source_type
    if task_id:
        artifact["task_id"] = task_id
    return _without_none(artifact)


def _srpm_cross_check_summary(cross_check):
    if not cross_check or cross_check.get("status") == "skipped":
        return {"status": "skipped"}
    return {
        "status": cross_check.get("status"),
        "file_sha256_equal": cross_check.get("file_sha256_equal"),
        "published_srpm_sha256": cross_check.get("published_srpm_sha256"),
        "koji_task_srpm_sha256": cross_check.get("koji_task_srpm_sha256"),
    }


def _build_environment_summary(
    args,
    resolution=None,
    repo_probe=None,
    mock_cfg=None,
    repo_fallback=None,
    executor=None,
):
    buildroot = resolution.buildroot if resolution else {}
    environment = {
        "executor": executor or _environment_executor_summary(args),
        "koji_server": args.koji_server,
        "koji_topurl": args.koji_topurl,
        "buildroot_id": buildroot.get("id"),
        "buildroot_tag": buildroot.get("tag_name"),
        "buildroot_arch": buildroot.get("arch"),
        "buildroot_repo_id": buildroot.get("repo_id"),
        "historical_repo_url": repo_probe.get("url") if repo_probe else None,
        "historical_repo_available": repo_probe.get("status") == 200 if repo_probe else None,
    }
    if mock_cfg:
        environment["mock_config"] = _public_artifact(summarize_file(mock_cfg))
    if repo_fallback:
        environment["repo_fallback"] = summarize_fallback_report(repo_fallback)
    return _without_none(environment)


def _environment_executor_summary(args):
    return {"mode": getattr(args, "executor", "local")}


def _rebuild_summary(args, status, rebuilds=None, repeatable=None, reason=None, error=None):
    summary = {
        "tool": "mock",
        "status": status,
        "runs": args.runs,
        "isolation": args.isolation,
    }
    if reason:
        summary["reason"] = reason
    if error:
        summary["error"] = error
    if repeatable is not None:
        summary["repeatable_by_rpm_sha256"] = repeatable
    if rebuilds:
        diagnosis = _first_failure_diagnosis(rebuilds)
        if diagnosis:
            summary["failure_diagnosis"] = diagnosis
        summary["runs_detail"] = [
            _without_none(
                {
                    "run": item.get("run"),
                    "exit_code": item.get("exit_code"),
                    "elapsed_seconds": item.get("elapsed_seconds"),
                    "rpms": [_public_artifact(rpm) for rpm in item.get("rpms", [])],
                    "failure_diagnosis": item.get("failure_diagnosis"),
                }
            )
            for item in rebuilds
        ]
    return summary


def _first_failure_diagnosis(rebuilds):
    for item in rebuilds or []:
        diagnosis = item.get("failure_diagnosis")
        if diagnosis:
            return diagnosis
    return None


def _print_rebuild_failure_diagnosis(result):
    diagnosis = result.get("failure_diagnosis")
    if not diagnosis:
        return
    print(f"[guanfu] Mock failure diagnosis: {diagnosis.get('summary')}", file=sys.stderr)
    print(f"[guanfu] Suggested action: {diagnosis.get('suggested_action')}", file=sys.stderr)
    evidence = diagnosis.get("evidence") or []
    if evidence:
        item = evidence[0]
        print(f"[guanfu] Evidence: {item.get('log')}: {item.get('line')}", file=sys.stderr)


def _unavailable_assessment(field, confidence=0.9):
    return {
        "overall_assessment": {
            "risk_level": "critical",
            "action": "reject",
            "reproducible": False,
            "confidence": confidence,
            "trust_level": "L0",
        },
        "diff_items": [
            {
                "diff_type": "OTHER",
                "risk_level": "critical",
                "fields": [field],
            }
        ],
        "summary_stats": {
            "total_diff_types": 1,
            "total_diff_items": 1,
            "diff_by_risk_level": {
                "none": 0,
                "low": 0,
                "medium": 0,
                "high": 0,
                "critical": 1,
            },
            "diff_by_type": {"OTHER": 1},
            "needs_deep_analysis_count": 0,
        },
    }


def _analysis_summary():
    return {
        "mode": "light",
        "capabilities": {
            "rpm_header": True,
            "rpm_file_manifest": True,
            "rpm_scriptlet": True,
            "path_based_classification": True,
            "elf_section_compare": False,
            "elf_buildid_compare": False,
            "payload_decompression_compare": False,
        },
        "unsupported_precise_types": ["BINARY_CODE", "BINARY_BUILDID"],
    }


def _find_report_paths(workdir, rpm_name):
    base = Path(workdir).expanduser().resolve() / _safe_name(Path(rpm_name).name)
    if not base.exists():
        return []
    return sorted(base.glob("**/report.json"))


def _write_preflight_report(args, status, reason, resolution=None, executor=None, error=None, assessment_field=None):
    run_dir = _run_dir(args.workdir, args.rpm_name)
    target_name = rpm_filename(resolution.rpm) if resolution else args.rpm_name
    report = {
        "version": ASSESSMENT_VERSION,
        "metadata": {
            "package_name": target_name,
            "analysis_time": _analysis_time(),
        },
        "input_artifacts": {},
        "build_environment": _build_environment_summary(
            args,
            resolution=resolution,
            executor=executor,
        ),
        "rebuild": _rebuild_summary(args, status, reason=reason, error=error),
        "analysis": _analysis_summary(),
    }
    if assessment_field:
        report.update(_unavailable_assessment(assessment_field))
    write_json(run_dir / "report.json", report)
    return run_dir / "report.json"


def _input_artifacts_summary(
    published_rpm_summary=None,
    task_srpm_summary=None,
    log_summaries=None,
    source_rpm_summary=None,
    source_rpm_type=None,
    source_rpm_url=None,
    task_id=None,
    srpm_cross_check=None,
):
    artifacts = {
        "reference_rpm": _public_artifact(
            published_rpm_summary,
            source_type="published_binary_repo",
        ),
        "source_rpm": _public_artifact(
            source_rpm_summary,
            source_type=source_rpm_type,
            url=source_rpm_url,
            task_id=task_id if source_rpm_type == "koji_task_output" else None,
        ),
        "koji_task_srpm": _public_artifact(
            task_srpm_summary,
            source_type="koji_task_output",
            task_id=task_id,
        ),
        "koji_logs": [
            _public_artifact(item, source_type="koji_task_output", task_id=task_id)
            for item in (log_summaries or [])
        ],
        "source_rpm_cross_check": _srpm_cross_check_summary(srpm_cross_check),
    }
    return _without_none(artifacts)


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

    executor = getattr(args, "executor", "vm")

    run_dir = _run_dir(args.workdir, args.rpm_name)
    inputs_dir = run_dir / "inputs"
    results_dir = run_dir / "results"
    metadata_dir = run_dir / "metadata"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "version": ASSESSMENT_VERSION,
        "metadata": {
            "package_name": args.rpm_name,
            "analysis_time": _analysis_time(),
        },
        "input_artifacts": {},
        "build_environment": _build_environment_summary(args),
        "rebuild": _rebuild_summary(args, "started"),
        "analysis": _analysis_summary(),
    }

    try:
        rpm_info = parse_rpm_filename(args.rpm_name)
        client = KojiClient(args.koji_server)
        resolution = resolve_koji_build(client, rpm_info)
        target_rpm_name = rpm_filename(resolution.rpm)
        target_os = detect_target_os(rpm_info, resolution.buildroot)

        if executor == "vm" and not is_supported_target_os(target_os):
            report = {
                "version": ASSESSMENT_VERSION,
                "metadata": {
                    "package_name": target_rpm_name,
                    "analysis_time": _analysis_time(),
                },
                "input_artifacts": {},
                "build_environment": _build_environment_summary(
                    args,
                    resolution=resolution,
                    executor=vm_executor_summary(target_os=target_os),
                ),
                "rebuild": _rebuild_summary(
                    args,
                    "unsupported",
                    reason="only an23 Koji RPM rebuild is currently supported by the VM executor",
                ),
                "analysis": _analysis_summary(),
            }
            report.update(_unavailable_assessment("unsupported_target"))
            write_json(run_dir / "report.json", report)
            print(
                "[guanfu] Unsupported Koji RPM target for VM executor: "
                f"tag={resolution.buildroot.get('tag_name')!r}",
                file=sys.stderr,
            )
            return 3

        write_json(metadata_dir / "rpm.json", resolution.rpm)
        write_json(metadata_dir / "build.json", resolution.build)
        write_json(metadata_dir / "buildroot.json", resolution.buildroot)
        write_json(metadata_dir / "buildarch-task.json", resolution.buildarch_task)
        write_json(metadata_dir / "task-result.json", resolution.task_result)

        repo_probe = probe_repodata(args.koji_topurl, resolution.buildroot)
        write_json(metadata_dir / "repo-probe.json", repo_probe)

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
        koji_recorded_env = parse_koji_recorded_environment(resolution, inputs_dir)

        srpm_for_rebuild = published_srpm
        source_rpm_summary = published_srpm_summary
        source_rpm_type = "published_source_repo"
        source_rpm_url = published_srpm_url
        if not srpm_for_rebuild:
            srpm_for_rebuild = task_srpm
            source_rpm_summary = task_srpm_summary
            source_rpm_type = "koji_task_output"
            source_rpm_url = None

        srpm_cross_check = compare_srpms(published_srpm, task_srpm)

        mock_cfg = inputs_dir / "mock.cfg"
        generate_mock_config(
            args.koji_server,
            args.koji_topurl,
            resolution.buildroot["id"],
            mock_cfg,
        )
        active_mock_cfg = mock_cfg
        repo_fallback = None

        if repo_probe.get("status") != 200:
            if getattr(args, "repo_fallback", "installed-pkgs") == "none":
                report = {
                    "version": ASSESSMENT_VERSION,
                    "metadata": {
                        "package_name": target_rpm_name,
                        "reference_url": published_rpm_url,
                        "reference_sha256": published_rpm_summary.get("sha256"),
                        "rebuild_sha256": None,
                        "analysis_time": _analysis_time(),
                    },
                    "input_artifacts": _input_artifacts_summary(
                        published_rpm_summary=published_rpm_summary,
                        task_srpm_summary=task_srpm_summary,
                        log_summaries=log_summaries,
                        source_rpm_summary=source_rpm_summary,
                        source_rpm_type=source_rpm_type,
                        source_rpm_url=source_rpm_url,
                        task_id=resolution.buildarch_task["id"],
                        srpm_cross_check=srpm_cross_check,
                    ),
                    "build_environment": _build_environment_summary(
                        args,
                        resolution=resolution,
                        repo_probe=repo_probe,
                        mock_cfg=mock_cfg,
                        executor=(
                            vm_executor_summary(target_os=target_os, koji_recorded=koji_recorded_env)
                            if executor == "vm"
                            else None
                        ),
                    ),
                    "rebuild": _rebuild_summary(
                        args,
                        "skipped",
                        reason="original buildroot repo repomd.xml is not available",
                    ),
                    "analysis": _analysis_summary(),
                }
                report.update(_unavailable_assessment("historical_repo"))
                write_json(run_dir / "report.json", report)
                print(f"[guanfu] Historical repo is not available: {repo_probe.get('url')}", file=sys.stderr)
                return 3

            installed_pkgs_log = inputs_dir / "installed_pkgs.log"
            if not installed_pkgs_log.exists():
                repo_fallback = {
                    "strategy": "installed_pkgs_log",
                    "status": "unavailable",
                    "source_task_id": resolution.buildarch_task["id"],
                    "dependency_recovery": {
                        "total": 0,
                        "resolved_by_getRPM": 0,
                        "unresolved": 0,
                        "payloadhash_mismatch": 0,
                        "task_output_available": 0,
                        "missing_task_output": 0,
                        "downloaded": 0,
                        "download_errors": 0,
                    },
                    "error": "installed_pkgs.log was not found in Koji task output",
                }
            else:
                try:
                    repo_fallback = prepare_installed_pkgs_fallback(
                        client,
                        resolution.buildroot,
                        installed_pkgs_log,
                        mock_cfg,
                        inputs_dir / "mock-fallback-installed-pkgs.cfg",
                        run_dir / "fallback-repo",
                        metadata_dir,
                        resolution.buildarch_task["id"],
                    )
                except Exception as exc:
                    repo_fallback = {
                        "strategy": "installed_pkgs_log",
                        "status": "error",
                        "source_task_id": resolution.buildarch_task["id"],
                        "installed_pkgs_log": _public_artifact(
                            summarize_file(installed_pkgs_log),
                            source_type="koji_task_output",
                            task_id=resolution.buildarch_task["id"],
                        ),
                        "dependency_recovery": {
                            "total": 0,
                            "resolved_by_getRPM": 0,
                            "unresolved": 0,
                            "payloadhash_mismatch": 0,
                            "task_output_available": 0,
                            "missing_task_output": 0,
                            "downloaded": 0,
                            "download_errors": 0,
                        },
                        "error": repr(exc),
                    }
            write_json(metadata_dir / "repo-fallback.json", repo_fallback)

            if repo_fallback.get("status") != "ready":
                report = {
                    "version": ASSESSMENT_VERSION,
                    "metadata": {
                        "package_name": target_rpm_name,
                        "reference_url": published_rpm_url,
                        "reference_sha256": published_rpm_summary.get("sha256"),
                        "rebuild_sha256": None,
                        "analysis_time": _analysis_time(),
                    },
                    "input_artifacts": _input_artifacts_summary(
                        published_rpm_summary=published_rpm_summary,
                        task_srpm_summary=task_srpm_summary,
                        log_summaries=log_summaries,
                        source_rpm_summary=source_rpm_summary,
                        source_rpm_type=source_rpm_type,
                        source_rpm_url=source_rpm_url,
                        task_id=resolution.buildarch_task["id"],
                        srpm_cross_check=srpm_cross_check,
                    ),
                    "build_environment": _build_environment_summary(
                        args,
                        resolution=resolution,
                        repo_probe=repo_probe,
                        mock_cfg=mock_cfg,
                        repo_fallback=repo_fallback,
                        executor=(
                            vm_executor_summary(target_os=target_os, koji_recorded=koji_recorded_env)
                            if executor == "vm"
                            else None
                        ),
                    ),
                    "rebuild": _rebuild_summary(
                        args,
                        "skipped",
                        reason="historical repo is unavailable and installed_pkgs fallback is incomplete",
                    ),
                    "analysis": _analysis_summary(),
                }
                report.update(_unavailable_assessment("dependency_recovery"))
                write_json(run_dir / "report.json", report)
                print(
                    "[guanfu] Historical repo is not available and installed_pkgs fallback is incomplete",
                    file=sys.stderr,
                )
                return 3

            active_mock_cfg = inputs_dir / "mock-fallback-installed-pkgs.cfg"

        executor_details = None
        if executor == "vm":
            vm_result = run_vm_rebuild(
                args,
                run_dir,
                active_mock_cfg,
                srpm_for_rebuild,
                results_dir,
                target_os,
                koji_recorded=koji_recorded_env,
            )
            rebuilds = vm_result["rebuilds"]
            executor_details = vm_result["executor"]
            if rebuilds and rebuilds[-1]["exit_code"] != 0:
                _print_rebuild_failure_diagnosis(rebuilds[-1])
        else:
            rebuilds = []
            for run_index in range(1, args.runs + 1):
                resultdir = results_dir / f"result-run-{run_index}"
                result = run_rebuild(active_mock_cfg, srpm_for_rebuild, resultdir, isolation=args.isolation)
                result["run"] = run_index
                rebuilds.append(result)
                if result["exit_code"] != 0:
                    _print_rebuild_failure_diagnosis(result)
                    break

        successful = rebuilds and all(item["exit_code"] == 0 for item in rebuilds)
        repeatable = None
        if successful:
            first_run_rpms = [Path(item.get("path", item["file"])) for item in rebuilds[0]["rpms"]]
            comparison = compare_published_and_rebuilt(
                published_rpm,
                first_run_rpms,
                target_rpm_name,
                reference_url=published_rpm_url,
            )
            if len(rebuilds) > 1:
                first = [(rpm["file"], rpm["sha256"]) for rpm in rebuilds[0]["rpms"]]
                repeatable = all(
                    [(rpm["file"], rpm["sha256"]) for rpm in rebuild["rpms"]] == first
                    for rebuild in rebuilds[1:]
                )

            report = {
                "version": ASSESSMENT_VERSION,
                "metadata": comparison["metadata"],
                "input_artifacts": _input_artifacts_summary(
                    published_rpm_summary=published_rpm_summary,
                    task_srpm_summary=task_srpm_summary,
                    log_summaries=log_summaries,
                    source_rpm_summary=source_rpm_summary,
                    source_rpm_type=source_rpm_type,
                    source_rpm_url=source_rpm_url,
                    task_id=resolution.buildarch_task["id"],
                    srpm_cross_check=srpm_cross_check,
                ),
                "build_environment": _build_environment_summary(
                    args,
                    resolution=resolution,
                    repo_probe=repo_probe,
                    mock_cfg=active_mock_cfg,
                    repo_fallback=repo_fallback,
                    executor=executor_details,
                ),
                "rebuild": _rebuild_summary(
                    args,
                    "rebuilt",
                    rebuilds=rebuilds,
                    repeatable=repeatable,
                ),
                "analysis": comparison["analysis"],
                "overall_assessment": comparison["overall_assessment"],
                "diff_items": comparison["diff_items"],
                "summary_stats": comparison["summary_stats"],
            }
        else:
            report = {
                "version": ASSESSMENT_VERSION,
                "metadata": {
                    "package_name": target_rpm_name,
                    "reference_url": published_rpm_url,
                    "reference_sha256": published_rpm_summary.get("sha256"),
                    "rebuild_sha256": None,
                    "analysis_time": _analysis_time(),
                },
                "input_artifacts": _input_artifacts_summary(
                    published_rpm_summary=published_rpm_summary,
                    task_srpm_summary=task_srpm_summary,
                    log_summaries=log_summaries,
                    source_rpm_summary=source_rpm_summary,
                    source_rpm_type=source_rpm_type,
                    source_rpm_url=source_rpm_url,
                    task_id=resolution.buildarch_task["id"],
                    srpm_cross_check=srpm_cross_check,
                ),
                "build_environment": _build_environment_summary(
                    args,
                    resolution=resolution,
                    repo_probe=repo_probe,
                    mock_cfg=active_mock_cfg,
                    repo_fallback=repo_fallback,
                    executor=executor_details,
                ),
                "rebuild": _rebuild_summary(args, "failed", rebuilds=rebuilds),
                "analysis": _analysis_summary(),
            }
            report.update(_unavailable_assessment("mock_rebuild", confidence=0.8))

        write_json(run_dir / "report.json", report)
        print(f"[guanfu] Koji RPM rebuild report: {run_dir / 'report.json'}")
        return 0 if successful else 1
    except Exception as exc:
        report["rebuild"] = _rebuild_summary(args, "error", error=repr(exc))
        report.update(_unavailable_assessment("rebuild_pipeline", confidence=0.7))
        write_json(run_dir / "report.json", report)
        print(f"[guanfu] ERROR: {exc}", file=sys.stderr)
        print(f"[guanfu] Partial report: {run_dir / 'report.json'}", file=sys.stderr)
        return 1
