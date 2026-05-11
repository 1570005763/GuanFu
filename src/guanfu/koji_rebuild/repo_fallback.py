import bz2
import gzip
import lzma
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from guanfu.koji_rebuild.downloader import download_task_output, download_url, summarize_file
from guanfu.koji_rebuild.rpm_name import rpm_filename


def _strip_epoch_from_nevra(nevra):
    return re.sub(r"(?<=-)\d+:", "", nevra)


def parse_installed_pkgs(path):
    entries = []
    for line_number, line in enumerate(Path(path).read_text(errors="replace").splitlines(), 1):
        parts = line.split()
        if len(parts) < 5:
            continue
        nevra = parts[0]
        entries.append(
            {
                "line": line_number,
                "nevra": nevra,
                "rpm_lookup": _strip_epoch_from_nevra(nevra),
                "buildtime": parts[1],
                "installed_size": parts[2],
                "payloadhash": parts[3],
                "state": parts[4],
            }
        )
    return entries


def _parse_nevra(nevra):
    try:
        nvr, arch = nevra.rsplit(".", 1)
        nv, release = nvr.rsplit("-", 1)
        name, version = nv.rsplit("-", 1)
    except ValueError as exc:
        raise ValueError(f"Cannot parse NEVRA: {nevra}") from exc
    return {"name": name, "version": version, "release": release, "arch": arch}


def _rpm_filename_from_nevra(nevra):
    return "%s.rpm" % nevra


def _replace_repo_arch(url, arch):
    return url.replace("$arch", arch).replace("${arch}", arch).replace("$basearch", arch)


def _url_join(base, href):
    if not base.endswith("/"):
        base += "/"
    return urllib.parse.urljoin(base, href)


def _download_metadata(url, dest):
    path = download_url(url, dest)
    return summarize_file(path, label="external_repo_metadata", url=url)


def _open_metadata(path):
    path = Path(path)
    name = path.name
    if name.endswith(".gz"):
        return gzip.open(path, "rb")
    if name.endswith(".bz2"):
        return bz2.open(path, "rb")
    if name.endswith(".xz"):
        return lzma.open(path, "rb")
    return path.open("rb")


def _local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def _find_primary_location(repomd_path):
    tree = ET.parse(repomd_path)
    for data in tree.getroot():
        if _local_name(data) != "data" or data.get("type") != "primary":
            continue
        location = None
        checksum = None
        open_checksum = None
        for child in data:
            name = _local_name(child)
            if name == "location":
                location = child.get("href")
            elif name == "checksum":
                checksum = {"type": child.get("type"), "value": child.text}
            elif name == "open-checksum":
                open_checksum = {"type": child.get("type"), "value": child.text}
        if location:
            return {"href": location, "checksum": checksum, "open_checksum": open_checksum}
    raise RuntimeError("external repo repomd.xml does not contain primary metadata")


def _parse_primary_packages(primary_path):
    with _open_metadata(primary_path) as metadata:
        for _, package in ET.iterparse(metadata, events=("end",)):
            if _local_name(package) != "package" or package.get("type") != "rpm":
                continue
            item = {}
            for child in package:
                name = _local_name(child)
                if name == "name":
                    item["name"] = child.text
                elif name == "arch":
                    item["arch"] = child.text
                elif name == "version":
                    item["epoch"] = child.get("epoch")
                    item["version"] = child.get("ver")
                    item["release"] = child.get("rel")
                elif name == "checksum":
                    item["checksum"] = {"type": child.get("type"), "value": child.text}
                elif name == "location":
                    item["href"] = child.get("href")
                elif name == "size":
                    item["package_size"] = child.get("package")
                    item["installed_size"] = child.get("installed")
                    item["archive_size"] = child.get("archive")
                elif name == "time":
                    item["file_time"] = child.get("file")
                    item["buildtime"] = child.get("build")
            yield item
            package.clear()


def _rpm_query(path):
    query = (
        "NAME=%{NAME}\\n"
        "VERSION=%{VERSION}\\n"
        "RELEASE=%{RELEASE}\\n"
        "ARCH=%{ARCH}\\n"
        "BUILDTIME=%{BUILDTIME}\\n"
        "SIZE=%{SIZE}\\n"
        "SIGMD5=%{SIGMD5}\\n"
        "SOURCERPM=%{SOURCERPM}\\n"
        "VENDOR=%{VENDOR}\\n"
        "PACKAGER=%{PACKAGER}\\n"
    )
    proc = subprocess.run(
        ["rpm", "-qp", "--qf", query, str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    result = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.lower()] = value
    return result


def _verify_external_rpm(path, entry):
    expected = _parse_nevra(entry["rpm_lookup"])
    header = _rpm_query(path)
    checks = {
        "nevra": all(
            str(header.get(key)) == str(expected[key])
            for key in ("name", "version", "release", "arch")
        ),
        "sigmd5": str(header.get("sigmd5", "")).lower() == str(entry.get("payloadhash", "")).lower(),
        "size": str(header.get("size")) == str(entry.get("installed_size")),
        "buildtime": str(header.get("buildtime")) == str(entry.get("buildtime")),
    }
    return {
        "checks": checks,
        "matched": all(checks.values()),
        "rpm_header": header,
    }


def _task_output_has_file(outputs, filename):
    if isinstance(outputs, dict):
        return filename in outputs
    return filename in (outputs or [])


def _find_task_output(client, build_task_id, filename, cache):
    if build_task_id in cache:
        task_outputs = cache[build_task_id]
    else:
        task_outputs = []
        try:
            task_outputs.append((build_task_id, client.list_task_output(build_task_id)))
        except Exception:
            pass
        for child in client.get_task_children(build_task_id):
            task_id = child.get("id")
            if task_id is None:
                continue
            try:
                task_outputs.append((task_id, client.list_task_output(task_id)))
            except Exception:
                continue
        cache[build_task_id] = task_outputs

    for task_id, outputs in task_outputs:
        if _task_output_has_file(outputs, filename):
            return task_id
    return None


def _download_dependency(client, task_id, filename, repo_dir):
    path = Path(repo_dir) / filename
    if path.exists() and path.stat().st_size > 0:
        return summarize_file(path, label="recovered_dependency_rpm")
    download_task_output(client, task_id, filename, path)
    return summarize_file(path, label="recovered_dependency_rpm")


def _repo_event(buildroot):
    return buildroot.get("repo_create_event_id") or buildroot.get("create_event_id")


def _external_repo_metadata(client, buildroot, metadata_dir):
    event = _repo_event(buildroot)
    repos = client.get_external_repo_list(buildroot["tag_name"], event)
    arch = buildroot["arch"]
    metadata = []
    for index, repo in enumerate(repos or []):
        base_url = _replace_repo_arch(repo["url"], arch)
        repo_dir = Path(metadata_dir) / "external-repos" / ("%02d-%s" % (index, repo["external_repo_name"]))
        repo_dir.mkdir(parents=True, exist_ok=True)
        item = dict(repo)
        item["event_id"] = event
        item["resolved_url"] = base_url
        try:
            repomd_url = _url_join(base_url, "repodata/repomd.xml")
            repomd = _download_metadata(repomd_url, repo_dir / "repomd.xml")
            primary_info = _find_primary_location(repo_dir / "repomd.xml")
            primary_url = _url_join(base_url, primary_info["href"])
            primary = _download_metadata(primary_url, repo_dir / Path(primary_info["href"]).name)
            item["repomd"] = repomd
            item["primary"] = primary
            item["primary_info"] = primary_info
            item["status"] = "ready"
        except Exception as exc:
            item["status"] = "error"
            item["error"] = repr(exc)
        metadata.append(item)
    return metadata


def _find_external_package(repo_metadata, entry):
    expected = _parse_nevra(entry["rpm_lookup"])
    for repo in repo_metadata:
        if repo.get("status") != "ready":
            continue
        for package in _parse_primary_packages(repo["primary"]["path"]):
            if all(
                str(package.get(key)) == str(expected[key])
                for key in ("name", "version", "release", "arch")
            ):
                return repo, package
    return None, None


def _recover_external_rpms(client, buildroot, entries, repo_dir, metadata_dir):
    repo_metadata = _external_repo_metadata(client, buildroot, metadata_dir)
    report = {
        "status": "ready",
        "event_id": _repo_event(buildroot),
        "repos": repo_metadata,
        "resolved": [],
        "unresolved": [],
        "verification_failed": [],
        "download_errors": [],
    }
    recovered = []
    downloaded_cache = {}
    for entry in entries:
        repo, package = _find_external_package(repo_metadata, entry)
        if not package:
            report["unresolved"].append(entry)
            continue

        source_url = _url_join(repo["resolved_url"], package["href"])
        filename = Path(package["href"]).name or _rpm_filename_from_nevra(entry["rpm_lookup"])
        path = Path(repo_dir) / filename
        artifact = downloaded_cache.get(source_url)
        if artifact is None:
            try:
                download_url(source_url, path)
                artifact = summarize_file(path, label="recovered_external_rpm", url=source_url)
                artifact["source_type"] = "external_repo"
                artifact["external_repo_name"] = repo["external_repo_name"]
                artifact["repo_checksum"] = package.get("checksum")
                downloaded_cache[source_url] = artifact
            except Exception as exc:
                report["download_errors"].append(
                    {
                        "nevra": entry["nevra"],
                        "url": source_url,
                        "error": repr(exc),
                    }
                )
                continue

        verification = _verify_external_rpm(path, entry)
        item = {
            "entry": entry,
            "source_type": "external_repo",
            "external_repo_name": repo["external_repo_name"],
            "source_url": source_url,
            "filename": filename,
            "artifact": artifact,
            "package_metadata": package,
            "verification": verification,
        }
        if not verification["matched"]:
            report["verification_failed"].append(
                {
                    "nevra": entry["nevra"],
                    "url": source_url,
                    "verification": verification,
                }
            )
            continue
        report["resolved"].append(
            {
                "nevra": entry["nevra"],
                "rpm_lookup": entry["rpm_lookup"],
                "source_repo": repo["external_repo_name"],
                "url": source_url,
                "verification": verification,
                "artifact": artifact,
            }
        )
        recovered.append(item)

    if report["unresolved"] or report["verification_failed"] or report["download_errors"]:
        report["status"] = "incomplete"
    return recovered, report


def _write_comps(entries, dest):
    names = sorted(set(entry["name"] for entry in entries))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE comps PUBLIC "-//Red Hat, Inc.//DTD Comps info//EN" "comps.dtd">',
        "<comps>",
        "  <group>",
        "    <id>build</id>",
        "    <name>build</name>",
        "    <description>Reconstructed Koji buildroot package group</description>",
        "    <default>true</default>",
        "    <uservisible>false</uservisible>",
        "    <packagelist>",
    ]
    for name in names:
        lines.append('      <packagereq type="default">%s</packagereq>' % escape(name))
    lines.extend(
        [
            "    </packagelist>",
            "  </group>",
            "</comps>",
        ]
    )
    Path(dest).write_text("\n".join(lines) + "\n")


def _run_createrepo(repo_dir, comps_xml):
    createrepo = shutil.which("createrepo_c") or shutil.which("createrepo")
    if not createrepo:
        raise RuntimeError("createrepo_c or createrepo is required for installed_pkgs fallback")
    cmd = [createrepo, "-q", "-g", str(comps_xml), str(repo_dir)]
    subprocess.run(cmd, check=True)


def _load_mock_config_values(mock_cfg):
    defaults = {
        "package_manager": "dnf",
        "use_bootstrap": True,
        "use_bootstrap_image": True,
        "dnf4_install_command": "install python3-dnf python3-dnf-plugins-core",
        "dnf5_install_command": "install dnf5 dnf5-plugins",
        "yum_install_command": "install yum yum-utils",
        "microdnf_install_command": "dnf-install microdnf dnf dnf-plugins-core",
        "bootstrap_chroot_additional_packages": [],
    }
    try:
        from mockbuild.config import setup_default_config_opts, update_config_from_file

        config_opts = setup_default_config_opts()
        update_config_from_file(config_opts, str(mock_cfg))
        source = "mockbuild_config"
    except Exception as exc:
        config_opts = dict(defaults)
        config_opts["plugin_conf"] = {}
        config_opts["macros"] = {}
        source = "fallback_default"
        try:
            exec(compile(Path(mock_cfg).read_text(), str(mock_cfg), "exec"), {"config_opts": config_opts})
            source = "fallback_default_plus_mock_cfg"
        except Exception:
            config_opts["load_error"] = repr(exc)

    values = dict(defaults)
    for key in defaults:
        try:
            if key in config_opts:
                values[key] = config_opts[key]
        except Exception:
            pass
    values["source"] = source
    return values


def _package_manager_key(name):
    if name == "dnf":
        return "dnf4"
    return name


def _packages_from_install_command(command):
    if not command:
        return []
    tokens = shlex.split(command) if isinstance(command, str) else list(command)
    if tokens and tokens[0] in ("install", "dnf-install"):
        tokens = tokens[1:]
    return [token for token in tokens if token and not token.startswith("-")]


def _discover_bootstrap_toolchain(mock_cfg):
    config = _load_mock_config_values(mock_cfg)
    pm = _package_manager_key(str(config.get("package_manager") or "dnf"))
    install_command = config.get(f"{pm}_install_command")
    requested = _packages_from_install_command(install_command)
    requested.extend(config.get("bootstrap_chroot_additional_packages") or [])
    requested = sorted(set(requested))
    enabled = bool(config.get("use_bootstrap")) and bool(requested)
    return {
        "status": "pending" if enabled else "disabled",
        "source": config.get("source"),
        "package_manager": pm,
        "use_bootstrap": bool(config.get("use_bootstrap")),
        "use_bootstrap_image": bool(config.get("use_bootstrap_image")),
        "install_command": install_command,
        "requested_packages": requested,
    }


def _infer_releasever(buildroot):
    tag = buildroot.get("tag_name") or ""
    match = re.search(r"an(\d+)", tag)
    if match:
        return match.group(1)
    return None


def _dnf_download_bootstrap_toolchain(toolchain, buildroot, repo_dir, metadata_dir, external_repos):
    requested = toolchain.get("requested_packages") or []
    if not requested:
        toolchain["status"] = "disabled"
        return []

    dnf = shutil.which("dnf") or shutil.which("dnf-3")
    if not dnf:
        toolchain["status"] = "unavailable"
        toolchain["error"] = "dnf or dnf-3 is required to resolve mock bootstrap toolchain"
        return []

    installroot = Path(metadata_dir) / "bootstrap-installroot"
    installroot.mkdir(parents=True, exist_ok=True)
    download_dir = Path(metadata_dir) / "bootstrap-downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        dnf,
        "-y",
        "--nogpgcheck",
        "--disableplugin=releasever_adapter",
        "--setopt=reposdir=/dev/null",
        "--setopt=gpgcheck=0",
        "--setopt=repo_gpgcheck=0",
        "--setopt=install_weak_deps=0",
        "--installroot",
        str(installroot),
        "--downloaddir",
        str(download_dir),
        "--downloadonly",
        "--repofrompath",
        "fallback-buildroot,file://%s" % os.path.abspath(str(repo_dir)),
        "--enablerepo",
        "fallback-buildroot",
    ]
    releasever = _infer_releasever(buildroot)
    if releasever:
        cmd.extend(["--releasever", releasever])

    for index, repo in enumerate(external_repos or []):
        if repo.get("status") != "ready":
            continue
        repo_id = "external-%d-%s" % (index, re.sub(r"[^A-Za-z0-9_.-]+", "_", repo["external_repo_name"]))
        cmd.extend(["--repofrompath", "%s,%s" % (repo_id, repo["resolved_url"])])
        cmd.extend(["--enablerepo", repo_id])

    cmd.extend(["install"])
    cmd.extend(requested)

    before = set(Path(repo_dir).glob("*.rpm"))
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        toolchain["resolver_command"] = " ".join(shlex.quote(part) for part in cmd)
        toolchain["resolver_output"] = proc.stdout[-8000:]
    except Exception as exc:
        toolchain["status"] = "incomplete"
        toolchain["error"] = repr(exc)
        if hasattr(exc, "stdout") and exc.stdout:
            toolchain["resolver_output"] = exc.stdout[-8000:]
        return []

    for path in sorted(download_dir.glob("*.rpm")):
        shutil.copy2(path, Path(repo_dir) / path.name)
    after = set(Path(repo_dir).glob("*.rpm"))
    new_paths = sorted(after - before)
    artifacts = []
    for path in new_paths:
        artifact = summarize_file(path, label="bootstrap_toolchain_rpm")
        artifact["source_type"] = "mock_bootstrap_toolchain"
        artifacts.append(artifact)
    toolchain["status"] = "ready"
    toolchain["releasever"] = releasever
    toolchain["downloaded"] = len(artifacts)
    toolchain["rpms"] = artifacts
    toolchain["historical_exactness"] = "not_proven"
    toolchain["repo_visibility"] = "visible_to_bootstrap_and_final_buildroot"
    return artifacts


def rewrite_mock_config_for_local_repo(source_cfg, dest_cfg, repo_dir, releasever=None):
    source_cfg = Path(source_cfg)
    dest_cfg = Path(dest_cfg)
    dest_cfg.parent.mkdir(parents=True, exist_ok=True)
    text = source_cfg.read_text()
    local_url = "file://%s" % os.path.abspath(str(repo_dir))
    rewritten, count = re.subn(r"baseurl=[^\\']+", "baseurl=%s" % local_url, text)
    if count == 0:
        raise RuntimeError("mock.cfg does not contain a repo baseurl to rewrite")
    if releasever and "config_opts['releasever']" not in rewritten and 'config_opts["releasever"]' not in rewritten:
        rewritten += "\n# Local fallback bootstrap resolver hint.\n"
        rewritten += "config_opts['releasever'] = '%s'\n" % releasever
    dest_cfg.write_text(rewritten)
    return dest_cfg


def _public_artifact(summary):
    if not summary:
        return None
    return dict(
        (key, summary[key])
        for key in (
            "file",
            "url",
            "size",
            "sha256",
            "label",
            "source_type",
            "task_id",
            "external_repo_name",
            "repo_checksum",
            "error",
        )
        if key in summary
    )


def _public_external_item(item):
    if not item:
        return None
    result = {}
    for key in ("nevra", "rpm_lookup", "source_repo", "url", "error"):
        if key in item:
            result[key] = item[key]
    if "verification" in item:
        verification = item["verification"]
        result["verification"] = {
            "checks": verification.get("checks"),
            "matched": verification.get("matched"),
            "rpm_header": verification.get("rpm_header"),
        }
    if "artifact" in item:
        result["artifact"] = _public_artifact(item["artifact"])
    return result


def summarize_fallback_report(report, max_items=20):
    recovery = report.get("dependency_recovery", {})
    local_repo = report.get("local_repo") or {}
    external = report.get("external_repo_recovery") or {}
    bootstrap = report.get("bootstrap_toolchain") or {}
    summary = {
        "strategy": report.get("strategy"),
        "status": report.get("status"),
        "error": report.get("error"),
        "source_task_id": report.get("source_task_id"),
        "installed_pkgs_log": _public_artifact(report.get("installed_pkgs_log")),
        "local_repo": {
            "rpm_count": local_repo.get("rpm_count"),
            "comps": _public_artifact(local_repo.get("comps")),
        }
        if local_repo
        else None,
        "mock_config": _public_artifact(report.get("mock_config")),
        "dependency_recovery": {
            "total": recovery.get("total"),
            "resolved_by_getRPM": recovery.get("resolved_by_getRPM"),
            "resolved_by_external_repo": recovery.get("resolved_by_external_repo"),
            "unresolved": recovery.get("unresolved"),
            "payloadhash_mismatch": recovery.get("payloadhash_mismatch"),
            "task_output_available": recovery.get("task_output_available"),
            "missing_task_output": recovery.get("missing_task_output"),
            "downloaded": recovery.get("downloaded"),
            "download_errors": recovery.get("download_errors"),
        },
        "external_repo_recovery": {
            "status": external.get("status"),
            "event_id": external.get("event_id"),
            "repo_count": len(external.get("repos") or []),
            "resolved": len(external.get("resolved") or []),
            "unresolved": len(external.get("unresolved") or []),
            "verification_failed": len(external.get("verification_failed") or []),
            "download_errors": len(external.get("download_errors") or []),
        }
        if external
        else None,
        "bootstrap_toolchain": {
            "status": bootstrap.get("status"),
            "source": bootstrap.get("source"),
            "package_manager": bootstrap.get("package_manager"),
            "install_command": bootstrap.get("install_command"),
            "requested_packages": bootstrap.get("requested_packages"),
            "downloaded": bootstrap.get("downloaded"),
            "releasever": bootstrap.get("releasever"),
            "historical_exactness": bootstrap.get("historical_exactness"),
            "repo_visibility": bootstrap.get("repo_visibility"),
            "error": bootstrap.get("error"),
        }
        if bootstrap
        else None,
    }
    for key in ("unresolved_items", "payloadhash_mismatch_items", "missing_task_output_items", "download_error_items"):
        values = recovery.get(key) or []
        if values:
            summary["dependency_recovery"][key] = values[:max_items]
            summary["dependency_recovery"][key + "_truncated"] = len(values) > max_items
    if external:
        for key in ("resolved", "unresolved", "verification_failed", "download_errors"):
            values = external.get(key) or []
            if values:
                if key == "resolved":
                    summary["external_repo_recovery"][key + "_items"] = [
                        _public_external_item(item) for item in values[:max_items]
                    ]
                else:
                    summary["external_repo_recovery"][key + "_items"] = values[:max_items]
                summary["external_repo_recovery"][key + "_truncated"] = len(values) > max_items
    return dict((key, value) for key, value in summary.items() if value is not None)


def prepare_installed_pkgs_fallback(
    client,
    buildroot,
    installed_pkgs_log,
    base_mock_cfg,
    fallback_mock_cfg,
    repo_dir,
    metadata_dir,
    source_task_id,
):
    installed_pkgs_log = Path(installed_pkgs_log)
    repo_dir = Path(repo_dir)
    metadata_dir = Path(metadata_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    entries = parse_installed_pkgs(installed_pkgs_log)
    rpm_cache = {}
    build_cache = {}
    task_output_cache = {}
    resolved = []
    unresolved_by_getrpm = []
    payload_mismatch = []
    missing_task_output = []

    for entry in entries:
        rpm = rpm_cache.get(entry["rpm_lookup"])
        if entry["rpm_lookup"] not in rpm_cache:
            rpm = client.get_rpm_optional(entry["rpm_lookup"])
            rpm_cache[entry["rpm_lookup"]] = rpm
        if not rpm:
            unresolved_by_getrpm.append(entry)
            continue
        if rpm.get("payloadhash") and rpm.get("payloadhash") != entry.get("payloadhash"):
            payload_mismatch.append(
                {
                    "nevra": entry["nevra"],
                    "rpm_lookup": entry["rpm_lookup"],
                    "installed_payloadhash": entry.get("payloadhash"),
                    "koji_payloadhash": rpm.get("payloadhash"),
                }
            )
            continue

        build_id = rpm["build_id"]
        build = build_cache.get(build_id)
        if build_id not in build_cache:
            build = client.get_build(build_id)
            build_cache[build_id] = build

        filename = rpm_filename(rpm)
        output_task_id = _find_task_output(client, build["task_id"], filename, task_output_cache)
        if output_task_id is None:
            missing_task_output.append(
                {
                    "nevra": entry["nevra"],
                    "filename": filename,
                    "build_id": build_id,
                    "build_task_id": build.get("task_id"),
                }
            )
            continue

        resolved.append(
            {
                "entry": entry,
                "name": rpm["name"],
                "rpm": rpm,
                "source_type": "koji_task_output",
                "build_id": build_id,
                "build_task_id": build.get("task_id"),
                "output_task_id": output_task_id,
                "filename": filename,
            }
        )

    external_resolved = []
    external_report = None
    if unresolved_by_getrpm and buildroot:
        external_resolved, external_report = _recover_external_rpms(
            client,
            buildroot,
            unresolved_by_getrpm,
            repo_dir,
            metadata_dir,
        )
        for item in external_resolved:
            parsed = _parse_nevra(item["entry"]["rpm_lookup"])
            item["name"] = parsed["name"]
        resolved.extend(external_resolved)

    unresolved = []
    if external_report:
        unresolved = external_report.get("unresolved") or []
    else:
        unresolved = unresolved_by_getrpm

    recovery = {
        "total": len(entries),
        "resolved_by_getRPM": len(entries) - len(unresolved_by_getrpm),
        "resolved_by_external_repo": len(external_resolved),
        "unresolved": len(unresolved),
        "payloadhash_mismatch": len(payload_mismatch),
        "task_output_available": len([item for item in resolved if item.get("source_type") == "koji_task_output"]),
        "missing_task_output": len(missing_task_output),
        "downloaded": 0,
        "download_errors": len((external_report or {}).get("download_errors") or []),
        "unresolved_items": unresolved,
        "unresolved_by_getRPM_items": unresolved_by_getrpm,
        "payloadhash_mismatch_items": payload_mismatch,
        "missing_task_output_items": missing_task_output,
        "download_error_items": list((external_report or {}).get("download_errors") or []),
    }
    report = {
        "strategy": "installed_pkgs_log",
        "status": "incomplete",
        "source_task_id": source_task_id,
        "installed_pkgs_log": summarize_file(installed_pkgs_log, label="koji_task_log"),
        "dependency_recovery": recovery,
    }
    if external_report:
        report["external_repo_recovery"] = external_report

    external_failed = external_report and external_report.get("status") != "ready"
    if unresolved or payload_mismatch or missing_task_output or external_failed:
        return report

    downloaded = []
    seen = set()
    for item in resolved:
        filename = item["filename"]
        if filename in seen:
            continue
        seen.add(filename)
        try:
            if item.get("source_type") == "external_repo":
                artifact = item["artifact"]
            else:
                artifact = _download_dependency(client, item["output_task_id"], filename, repo_dir)
                artifact["source_type"] = "koji_task_output"
                artifact["task_id"] = item["output_task_id"]
            downloaded.append(artifact)
        except Exception as exc:
            recovery["download_error_items"].append(
                {
                    "filename": filename,
                    "task_id": item["output_task_id"],
                    "error": repr(exc),
                }
            )

    recovery["downloaded"] = len(downloaded)
    recovery["download_errors"] = len(recovery["download_error_items"])
    if recovery["download_errors"]:
        return report

    try:
        comps_xml = metadata_dir / "fallback-comps.xml"
        _write_comps(resolved, comps_xml)
        _run_createrepo(repo_dir, comps_xml)

        bootstrap = _discover_bootstrap_toolchain(base_mock_cfg)
        if bootstrap.get("status") != "disabled":
            if not external_report and buildroot:
                external_report = {
                    "status": "ready",
                    "event_id": _repo_event(buildroot),
                    "repos": _external_repo_metadata(client, buildroot, metadata_dir),
                    "resolved": [],
                    "unresolved": [],
                    "verification_failed": [],
                    "download_errors": [],
                }
                if any(repo.get("status") != "ready" for repo in external_report["repos"]):
                    external_report["status"] = "partial"
                report["external_repo_recovery"] = external_report
            bootstrap_artifacts = _dnf_download_bootstrap_toolchain(
                bootstrap,
                buildroot or {},
                repo_dir,
                metadata_dir,
                (external_report or {}).get("repos") or [],
            )
            downloaded.extend(bootstrap_artifacts)
            if bootstrap.get("status") not in ("ready", "disabled"):
                report["bootstrap_toolchain"] = bootstrap
                return report
            _run_createrepo(repo_dir, comps_xml)
        report["bootstrap_toolchain"] = bootstrap

        rewrite_mock_config_for_local_repo(
            base_mock_cfg,
            fallback_mock_cfg,
            repo_dir,
            releasever=_infer_releasever(buildroot or {}),
        )
    except Exception as exc:
        report["status"] = "error"
        report["error"] = repr(exc)
        return report

    report["status"] = "ready"
    report["local_repo"] = {
        "path": str(repo_dir),
        "rpm_count": len(downloaded),
        "comps": summarize_file(comps_xml),
    }
    report["mock_config"] = summarize_file(fallback_mock_cfg)
    report["dependency_rpms"] = downloaded
    return report
