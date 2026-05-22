import re
import urllib.parse
from pathlib import Path

from guanfu.koji_rebuild.downloader import join_url


OPENANOLIS_BINARY_RPM_BASE_URL = "https://mirrors.openanolis.cn/anolis/23/os/x86_64/os/Packages/"
OPENANOLIS_SOURCE_RPM_BASE_URL = "https://mirrors.openanolis.cn/anolis/23/os/source/Packages/"


class KojiProfile:
    def __init__(
        self,
        profile_id,
        match_rules=None,
        artifact_locator="koji-topurl-packages",
        binary_rpm_base_url=None,
        source_rpm_base_url=None,
        mock_config_providers=None,
        executor_policy="local",
        supported_executors=None,
        repo_url_template=None,
        target_os=None,
        source="builtin",
    ):
        self.id = profile_id
        self.match_rules = match_rules or {}
        self.artifact_locator = artifact_locator
        self.binary_rpm_base_url = binary_rpm_base_url
        self.source_rpm_base_url = source_rpm_base_url
        self.mock_config_providers = mock_config_providers or ["koji-cli"]
        self.executor_policy = executor_policy
        self.supported_executors = supported_executors or [executor_policy]
        self.repo_url_template = repo_url_template
        self.target_os = target_os
        self.source = source

    def matches(self, args, rpm_info, resolution):
        rules = self.match_rules or {}
        if rules.get("always"):
            return True
        if rules.get("target_os") == "an23":
            tag = ((resolution.buildroot or {}).get("tag_name") or "").lower()
            release = ((rpm_info or {}).get("release") or "").lower()
            return _has_an23_marker(tag) or _has_an23_marker(release)

        checks = []
        if "release_regex" in rules:
            checks.append(_matches_regex(rules["release_regex"], (rpm_info or {}).get("release")))
        if "tag_regex" in rules:
            checks.append(_matches_regex(rules["tag_regex"], (resolution.buildroot or {}).get("tag_name")))
        if "name_regex" in rules:
            checks.append(_matches_regex(rules["name_regex"], (rpm_info or {}).get("name")))
        if "server_regex" in rules:
            checks.append(_matches_regex(rules["server_regex"], getattr(args, "koji_server", "")))
        return bool(checks) and all(checks)

    def resolve_executor(self, requested):
        if requested in (None, "auto"):
            return self.executor_policy
        return requested

    def supports_executor(self, executor):
        return executor in (self.supported_executors or [])

    def binary_rpm_url(self, args, resolution, target_rpm_name):
        explicit_base = getattr(args, "binary_rpm_base_url", None)
        if explicit_base:
            return join_url(explicit_base, target_rpm_name), "explicit-base-url"
        if self.binary_rpm_base_url:
            return join_url(self.binary_rpm_base_url, target_rpm_name), "profile-base-url"
        return _koji_packages_url(getattr(args, "koji_topurl"), resolution.build, resolution.rpm["arch"], target_rpm_name), self.artifact_locator

    def source_rpm_url(self, args, resolution):
        explicit_base = getattr(args, "source_rpm_base_url", None)
        if explicit_base:
            return join_url(explicit_base, resolution.task_srpm_name), "explicit-base-url"
        if self.source_rpm_base_url:
            return join_url(self.source_rpm_base_url, resolution.task_srpm_name), "profile-base-url"
        return _koji_packages_url(getattr(args, "koji_topurl"), resolution.build, "src", resolution.task_srpm_name), self.artifact_locator


def load_koji_profiles(paths):
    profiles = []
    for path in paths or []:
        profiles.extend(_load_profile_file(path))
    profiles.extend(_builtin_profiles())
    return profiles


def select_koji_profile(requested, profile_files, args, rpm_info, resolution):
    profiles = load_koji_profiles(profile_files)
    requested = requested or "auto"
    if requested != "auto":
        for profile in profiles:
            if profile.id == requested:
                return profile, [profile.id]
        raise RuntimeError("Koji profile was not found: %s" % requested)

    candidates = [profile for profile in profiles if profile.matches(args, rpm_info, resolution)]
    if candidates:
        return candidates[0], [profile.id for profile in candidates]
    generic = next((profile for profile in profiles if profile.id == "generic-koji"), None)
    if not generic:
        raise RuntimeError("generic-koji profile was not found")
    return generic, [generic.id]


def _builtin_profiles():
    return [
        KojiProfile(
            "openanolis-an23",
            match_rules={"target_os": "an23"},
            artifact_locator="profile-base-url",
            binary_rpm_base_url=OPENANOLIS_BINARY_RPM_BASE_URL,
            source_rpm_base_url=OPENANOLIS_SOURCE_RPM_BASE_URL,
            mock_config_providers=["koji-cli"],
            executor_policy="vm",
            supported_executors=["vm", "local"],
            target_os="an23",
            source="builtin",
        ),
        KojiProfile(
            "generic-koji",
            match_rules={"always": True},
            artifact_locator="koji-topurl-packages",
            mock_config_providers=["task-output-mock-config", "koji-cli"],
            executor_policy="local",
            supported_executors=["local"],
            source="builtin",
        ),
    ]


def _load_profile_file(path):
    path = Path(path).expanduser()
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load Koji profile files") from exc
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    raw_profiles = data.get("profiles", data if isinstance(data, list) else [])
    profiles = []
    for item in raw_profiles:
        if not isinstance(item, dict) or not item.get("id"):
            raise RuntimeError("Koji profile entries must be mappings with an id")
        profiles.append(
            KojiProfile(
                item["id"],
                match_rules=item.get("match_rules") or item.get("match") or {},
                artifact_locator=item.get("artifact_locator", "koji-topurl-packages"),
                binary_rpm_base_url=item.get("binary_rpm_base_url"),
                source_rpm_base_url=item.get("source_rpm_base_url"),
                mock_config_providers=item.get("mock_config_providers"),
                executor_policy=item.get("executor_policy", "local"),
                supported_executors=item.get("supported_executors"),
                repo_url_template=item.get("repo_url_template"),
                target_os=item.get("target_os"),
                source=str(path),
            )
        )
    return profiles


def _koji_packages_url(topurl, build, arch, filename):
    base = "{topurl}/packages/{name}/{version}/{release}/{arch}/".format(
        topurl=str(topurl).rstrip("/"),
        name=_quote_path(build["name"]),
        version=_quote_path(build["version"]),
        release=_quote_path(build["release"]),
        arch=_quote_path(arch),
    )
    return join_url(base, filename)


def _quote_path(value):
    return urllib.parse.quote(str(value), safe="")


def _matches_regex(pattern, value):
    if value is None:
        return False
    return bool(re.search(pattern, str(value)))


def _has_an23_marker(value):
    if not value:
        return False
    return bool(re.search(r"(^|[^a-z0-9])an23([^a-z0-9]|$)", value))
