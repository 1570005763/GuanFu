from pathlib import Path


class KojiResolution:
    def __init__(self, rpm, build, buildroot, buildarch_task, task_result, outputs, task_srpm_name):
        self.rpm = rpm
        self.build = build
        self.buildroot = buildroot
        self.buildarch_task = buildarch_task
        self.task_result = task_result
        self.outputs = outputs
        self.task_srpm_name = task_srpm_name


def _select_buildarch_task(client, build_task_id, arch, buildroot_id):
    children = client.get_task_children(build_task_id)
    candidates = [
        child
        for child in children
        if child.get("method") == "buildArch" and child.get("arch") == arch
    ]
    for child in candidates:
        try:
            result = client.get_task_result(child["id"])
        except Exception:
            continue
        if result and result.get("brootid") == buildroot_id:
            return child
    if candidates:
        return candidates[0]
    raise RuntimeError(f"no buildArch task for arch={arch} under task={build_task_id}")


def _select_task_srpm(task_result, outputs):
    srpms = [Path(path).name for path in task_result.get("srpms", [])]
    if not srpms:
        srpms = [name for name in outputs if name.endswith(".src.rpm")]
    if not srpms:
        raise RuntimeError("no src.rpm found in buildArch task output")
    return sorted(srpms)[0]


def resolve_koji_build(client, rpm_info):
    rpm = client.get_rpm(rpm_info)
    if not rpm:
        raise RuntimeError(f"RPM was not found in Koji: {rpm_info}")

    build = client.get_build(rpm["build_id"])
    buildroot = client.get_buildroot(rpm["buildroot_id"])
    buildarch = _select_buildarch_task(
        client,
        build["task_id"],
        buildroot["arch"],
        rpm.get("buildroot_id"),
    )
    task_result = client.get_task_result(buildarch["id"])
    outputs = client.list_task_output(buildarch["id"])
    task_srpm_name = _select_task_srpm(task_result, outputs)

    return KojiResolution(
        rpm=rpm,
        build=build,
        buildroot=buildroot,
        buildarch_task=buildarch,
        task_result=task_result,
        outputs=outputs,
        task_srpm_name=task_srpm_name,
    )
