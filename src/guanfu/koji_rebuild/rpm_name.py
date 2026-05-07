from pathlib import Path


def parse_rpm_filename(filename):
    rpm_name = Path(filename).name
    if not rpm_name.endswith(".rpm"):
        raise ValueError(f"RPM filename must end with .rpm: {filename}")
    stem = rpm_name[:-4]
    try:
        nvr, arch = stem.rsplit(".", 1)
        nv, release = nvr.rsplit("-", 1)
        name, version = nv.rsplit("-", 1)
    except ValueError as exc:
        raise ValueError(f"Cannot parse RPM filename as name-version-release.arch.rpm: {filename}") from exc
    if not all([name, version, release, arch]):
        raise ValueError(f"Cannot parse RPM filename as name-version-release.arch.rpm: {filename}")
    return {
        "name": name,
        "version": version,
        "release": release,
        "arch": arch,
    }


def rpm_filename(rpm):
    return f"{rpm['name']}-{rpm['version']}-{rpm['release']}.{rpm['arch']}.rpm"
