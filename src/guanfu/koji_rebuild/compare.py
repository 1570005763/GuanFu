import subprocess
from datetime import datetime
from pathlib import Path

from guanfu.koji_rebuild.assessment import ASSESSMENT_VERSION, build_light_assessment
from guanfu.koji_rebuild.downloader import sha256_file


MAX_DIFF_ITEMS = 50


def _run_text(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return proc.stdout, None


def _analysis_time():
    return datetime.now().astimezone().isoformat()


def _rpm_query(path):
    query = (
        "NVRA=%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n"
        "BUILDTIME=%{BUILDTIME}\\n"
        "BUILDHOST=%{BUILDHOST}\\n"
        "SOURCERPM=%{SOURCERPM}\\n"
        "PAYLOADDIGEST=%{PAYLOADDIGEST}\\n"
        "PAYLOADCOMPRESSOR=%{PAYLOADCOMPRESSOR}\\n"
        "PAYLOADFLAGS=%{PAYLOADFLAGS}\\n"
        "SIGMD5=%{SIGMD5}\\n"
        "SHA256HEADER=%{SHA256HEADER}\\n"
        "RSAHEADER=%{RSAHEADER:pgpsig}\\n"
        "SIZE=%{SIZE}\\n"
    )
    out, err = _run_text(["rpm", "-qp", "--qf", query, str(path)])
    if err:
        return {"error": err}
    result = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key] = value
    return result


def _rpm_command_lines(path, option):
    out, err = _run_text(["rpm", "-qp", option, str(path)])
    if err:
        return {"error": err}
    return sorted(out.splitlines())


def _rpm_dump(path):
    out, err = _run_text(["rpm", "-qp", "--dump", str(path)])
    if err:
        return {"error": err}
    return sorted(out.splitlines())


def _rpm_dump_map(path):
    out, err = _run_text(["rpm", "-qp", "--dump", str(path)])
    if err:
        return None, err

    files = {}
    unparsed = []
    for line in out.splitlines():
        parsed = _parse_dump_line(line)
        if not parsed:
            unparsed.append(line)
            continue
        files[parsed["path"]] = parsed
    return {"files": files, "unparsed": unparsed}, None


def _parse_dump_line(line):
    parts = line.split()
    if len(parts) < 11:
        return None
    path = parts[0]
    return {
        "path": path,
        "size": parts[1],
        "mtime": parts[2],
        "digest": parts[3],
        "mode": parts[4],
        "owner": parts[5],
        "group": parts[6],
        "isconfig": parts[7],
        "isdoc": parts[8],
        "rdev": parts[9],
        "linkto": " ".join(parts[10:]),
    }


def _find_rebuilt_rpm(result_rpms, target_filename):
    for rpm in result_rpms:
        if Path(rpm).name == target_filename:
            return Path(rpm)
    return None


def _diff_headers(published_headers, rebuilt_headers):
    fields = {}
    keys = sorted(set(published_headers) | set(rebuilt_headers))
    for key in keys:
        published = published_headers.get(key)
        rebuilt = rebuilt_headers.get(key)
        if published != rebuilt:
            fields[key] = {
                "published": published,
                "rebuilt": rebuilt,
            }
    return {
        "different": bool(fields),
        "fields": fields,
    }


def _file_brief(item):
    return {
        "size": item.get("size"),
        "mtime": item.get("mtime"),
        "digest": item.get("digest"),
        "mode": item.get("mode"),
        "owner": item.get("owner"),
        "group": item.get("group"),
        "isconfig": item.get("isconfig"),
        "isdoc": item.get("isdoc"),
        "rdev": item.get("rdev"),
        "linkto": item.get("linkto"),
    }


def _diff_file_entry(published, rebuilt):
    compared_fields = [
        "size",
        "mtime",
        "digest",
        "mode",
        "owner",
        "group",
        "isconfig",
        "isdoc",
        "rdev",
        "linkto",
    ]
    changed_fields = [
        field
        for field in compared_fields
        if published.get(field) != rebuilt.get(field)
    ]
    if not changed_fields:
        return None
    return {
        "path": published["path"],
        "changed_fields": changed_fields,
        "published": _file_brief(published),
        "rebuilt": _file_brief(rebuilt),
    }


def _limit_items(items, limit=MAX_DIFF_ITEMS):
    return {
        "count": len(items),
        "truncated": len(items) > limit,
        "items": items[:limit],
    }


def _diff_files(published_rpm, rebuilt_rpm):
    published_dump, published_err = _rpm_dump_map(published_rpm)
    rebuilt_dump, rebuilt_err = _rpm_dump_map(rebuilt_rpm)
    if published_err or rebuilt_err:
        return {
            "status": "error",
            "published_error": published_err,
            "rebuilt_error": rebuilt_err,
        }, None

    published_files = published_dump["files"]
    rebuilt_files = rebuilt_dump["files"]
    published_paths = set(published_files)
    rebuilt_paths = set(rebuilt_files)

    only_in_published = sorted(published_paths - rebuilt_paths)
    only_in_rebuilt = sorted(rebuilt_paths - published_paths)

    changed = []
    for path in sorted(published_paths & rebuilt_paths):
        diff = _diff_file_entry(published_files[path], rebuilt_files[path])
        if diff:
            changed.append(diff)

    mtime_only = [
        item
        for item in changed
        if item["changed_fields"] == ["mtime"]
    ]
    non_mtime = [
        item
        for item in changed
        if set(item["changed_fields"]) - {"mtime"}
    ]
    content_related = [
        item
        for item in changed
        if set(item["changed_fields"]) & {"digest", "size", "linkto"}
    ]

    public_diff = {
        "status": "compared",
        "published_file_count": len(published_files),
        "rebuilt_file_count": len(rebuilt_files),
        "only_in_published": _limit_items(only_in_published),
        "only_in_rebuilt": _limit_items(only_in_rebuilt),
        "changed": _limit_items(changed),
        "mtime_only_changed_count": len(mtime_only),
        "non_mtime_changed_count": len(non_mtime),
        "content_related_changed_count": len(content_related),
        "files_equal_ignoring_mtime": (
            not only_in_published
            and not only_in_rebuilt
            and len(non_mtime) == 0
        ),
        "unparsed": {
            "published_count": len(published_dump["unparsed"]),
            "rebuilt_count": len(rebuilt_dump["unparsed"]),
        },
    }
    analysis_input = {
        "only_in_published": only_in_published,
        "only_in_rebuilt": only_in_rebuilt,
        "changed": changed,
    }
    return public_diff, analysis_input


def compare_published_and_rebuilt(published_rpm, result_rpms, target_filename, reference_url=None):
    published = Path(published_rpm)
    rebuilt = _find_rebuilt_rpm(result_rpms, target_filename)
    if not rebuilt:
        return {
            "status": "missing_rebuilt_target_rpm",
            "target_filename": target_filename,
        }

    published_requires = _rpm_command_lines(published, "--requires")
    rebuilt_requires = _rpm_command_lines(rebuilt, "--requires")
    published_provides = _rpm_command_lines(published, "--provides")
    rebuilt_provides = _rpm_command_lines(rebuilt, "--provides")
    published_scripts = _rpm_command_lines(published, "--scripts")
    rebuilt_scripts = _rpm_command_lines(rebuilt, "--scripts")
    published_dump = _rpm_dump(published)
    rebuilt_dump = _rpm_dump(rebuilt)

    published_headers = _rpm_query(published)
    rebuilt_headers = _rpm_query(rebuilt)
    header_diff = _diff_headers(published_headers, rebuilt_headers)
    files_diff, file_analysis = _diff_files(published, rebuilt)
    published_sha256 = sha256_file(published)
    rebuilt_sha256 = sha256_file(rebuilt)
    rpm_file_sha256_equal = published_sha256 == rebuilt_sha256
    requires_equal = published_requires == rebuilt_requires
    provides_equal = published_provides == rebuilt_provides
    scripts_equal = published_scripts == rebuilt_scripts
    dump_equal = published_dump == rebuilt_dump
    assessment = build_light_assessment(
        rpm_file_sha256_equal,
        header_diff,
        files_diff,
        file_analysis,
        requires_equal,
        provides_equal,
        scripts_equal,
    )

    result = {
        "version": ASSESSMENT_VERSION,
        "status": "compared",
        "target_filename": target_filename,
        "metadata": {
            "package_name": target_filename,
            "reference_url": reference_url,
            "reference_sha256": published_sha256,
            "rebuild_sha256": rebuilt_sha256,
            "analysis_time": _analysis_time(),
        },
        "published": {
            "file": str(published),
            "sha256": published_sha256,
            "headers": published_headers,
        },
        "rebuilt": {
            "file": str(rebuilt),
            "sha256": rebuilt_sha256,
            "headers": rebuilt_headers,
        },
        "rpm_file_sha256_equal": rpm_file_sha256_equal,
        "requires_equal": requires_equal,
        "provides_equal": provides_equal,
        "scripts_equal": scripts_equal,
        "dump_equal": dump_equal,
        "differences": {
            "headers": header_diff,
            "files": files_diff,
        },
    }
    result.update(assessment)
    return result


def compare_srpms(published_srpm, task_srpm):
    if not published_srpm or not task_srpm:
        return {"status": "skipped"}
    published = Path(published_srpm)
    task = Path(task_srpm)
    return {
        "status": "compared",
        "published_srpm_sha256": sha256_file(published),
        "koji_task_srpm_sha256": sha256_file(task),
        "file_sha256_equal": sha256_file(published) == sha256_file(task),
        "published_headers": _rpm_query(published),
        "koji_task_headers": _rpm_query(task),
    }
