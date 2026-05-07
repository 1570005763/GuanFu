import subprocess
from pathlib import Path

from guanfu.koji_rebuild.downloader import sha256_file


def _run_text(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return proc.stdout, None


def _rpm_query(path):
    query = (
        "NVRA=%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n"
        "BUILDTIME=%{BUILDTIME}\\n"
        "BUILDHOST=%{BUILDHOST}\\n"
        "SOURCERPM=%{SOURCERPM}\\n"
        "PAYLOADDIGEST=%{PAYLOADDIGEST}\\n"
        "SIGMD5=%{SIGMD5}\\n"
        "SHA256HEADER=%{SHA256HEADER}\\n"
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


def _find_rebuilt_rpm(result_rpms, target_filename):
    for rpm in result_rpms:
        if Path(rpm).name == target_filename:
            return Path(rpm)
    return None


def compare_published_and_rebuilt(published_rpm, result_rpms, target_filename):
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

    return {
        "status": "compared",
        "target_filename": target_filename,
        "published": {
            "file": str(published),
            "sha256": sha256_file(published),
            "headers": _rpm_query(published),
        },
        "rebuilt": {
            "file": str(rebuilt),
            "sha256": sha256_file(rebuilt),
            "headers": _rpm_query(rebuilt),
        },
        "rpm_file_sha256_equal": sha256_file(published) == sha256_file(rebuilt),
        "requires_equal": published_requires == rebuilt_requires,
        "provides_equal": published_provides == rebuilt_provides,
        "scripts_equal": published_scripts == rebuilt_scripts,
        "dump_equal": published_dump == rebuilt_dump,
    }


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
