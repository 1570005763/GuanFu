import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from guanfu.koji_rebuild.command import _prepare_mock_config, run_koji_rpm_rebuild
from guanfu.koji_rebuild.downloader import build_tls_config
from guanfu.koji_rebuild.profiles import load_koji_profiles, select_koji_profile


def _resolution(tag="dist", release="21.al8"):
    return SimpleNamespace(
        buildroot={"tag_name": tag},
        build={
            "name": "anothertest",
            "version": "1.0.0",
            "release": release,
        },
        rpm={
            "name": "anothertest",
            "version": "1.0.0",
            "release": release,
            "arch": "x86_64",
        },
        task_srpm_name="anothertest-1.0.0-%s.src.rpm" % release,
        outputs={},
        buildarch_task={"id": 193},
    )


class KojiProfileTests(unittest.TestCase):
    def test_auto_profile_prefers_openanolis_an23(self):
        args = SimpleNamespace(koji_server="https://build.openanolis.cn/kojihub", koji_topurl="https://build.openanolis.cn/kojifiles")
        rpm_info = {"name": "zlib", "release": "3.an23"}

        profile, candidates = select_koji_profile("auto", [], args, rpm_info, _resolution(tag="dist-an23.0-build"))

        self.assertEqual(profile.id, "openanolis-an23")
        self.assertIn("openanolis-an23", candidates)
        self.assertEqual(profile.resolve_executor("auto"), "vm")

    def test_auto_profile_falls_back_to_generic(self):
        args = SimpleNamespace(koji_server="http://koji.example/kojihub", koji_topurl="http://koji.example/kojifiles")
        rpm_info = {"name": "sample", "release": "1.al8"}

        profile, candidates = select_koji_profile("auto", [], args, rpm_info, _resolution(tag="dist"))

        self.assertEqual(profile.id, "generic-koji")
        self.assertEqual(candidates, ["generic-koji"])
        self.assertEqual(profile.resolve_executor("auto"), "local")
        self.assertFalse(profile.supports_executor("vm"))

    def test_openanolis_profile_keeps_existing_mirror_defaults(self):
        args = SimpleNamespace(binary_rpm_base_url=None, source_rpm_base_url=None, koji_topurl="https://build.openanolis.cn/kojifiles")
        profile = [item for item in load_koji_profiles([]) if item.id == "openanolis-an23"][0]

        binary_url, binary_locator = profile.binary_rpm_url(args, _resolution(release="3.an23"), "zlib-1.2.13-3.an23.x86_64.rpm")
        source_url, source_locator = profile.source_rpm_url(args, _resolution(release="3.an23"))

        self.assertEqual(binary_locator, "profile-base-url")
        self.assertIn("mirrors.openanolis.cn/anolis/23/os/x86_64/os/Packages/zlib-1.2.13-3.an23.x86_64.rpm", binary_url)
        self.assertEqual(source_locator, "profile-base-url")
        self.assertIn("mirrors.openanolis.cn/anolis/23/os/source/Packages/anothertest-1.0.0-3.an23.src.rpm", source_url)

    def test_generic_profile_derives_koji_packages_urls(self):
        args = SimpleNamespace(binary_rpm_base_url=None, source_rpm_base_url=None, koji_topurl="http://koji.example/kojifiles")
        profile = [item for item in load_koji_profiles([]) if item.id == "generic-koji"][0]
        resolution = _resolution(release="21.al8")

        binary_url, binary_locator = profile.binary_rpm_url(args, resolution, "anothertest-1.0.0-21.al8.x86_64.rpm")
        source_url, source_locator = profile.source_rpm_url(args, resolution)

        self.assertEqual(binary_locator, "koji-topurl-packages")
        self.assertEqual(binary_url, "http://koji.example/kojifiles/packages/anothertest/1.0.0/21.al8/x86_64/anothertest-1.0.0-21.al8.x86_64.rpm")
        self.assertEqual(source_locator, "koji-topurl-packages")
        self.assertEqual(source_url, "http://koji.example/kojifiles/packages/anothertest/1.0.0/21.al8/src/anothertest-1.0.0-21.al8.src.rpm")

    def test_profile_file_has_priority_in_auto_selection(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            profile_file = Path(tmp) / "profiles.yaml"
            profile_file.write_text(
                "profiles:\n"
                "  - id: custom-koji\n"
                "    match_rules:\n"
                "      tag_regex: '^dist$'\n"
                "    executor_policy: local\n"
                "    mock_config_providers: [task-output-mock-config]\n"
            )
            args = SimpleNamespace(koji_server="http://koji.example/kojihub", koji_topurl="http://koji.example/kojifiles")

            profile, candidates = select_koji_profile("auto", [str(profile_file)], args, {"release": "1.al8"}, _resolution(tag="dist"))

        self.assertEqual(profile.id, "custom-koji")
        self.assertEqual(candidates[0], "custom-koji")

    def test_mock_config_log_provider_does_not_require_koji_cli(self):
        profile = [item for item in load_koji_profiles([]) if item.id == "generic-koji"][0]
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / "mock_config.log").write_text("config_opts['root'] = 'dist-67-16'\n")
            mock_cfg = inputs / "mock.cfg"

            path, source = _prepare_mock_config(
                profile,
                client=None,
                args=SimpleNamespace(koji_server="http://koji.example/kojihub", koji_topurl="http://koji.example/kojifiles"),
                resolution=_resolution(),
                inputs_dir=inputs,
                mock_cfg=mock_cfg,
            )
            contents = mock_cfg.read_text()

        self.assertEqual(path, mock_cfg)
        self.assertEqual(source["provider"], "task-output-mock-config")
        self.assertEqual(contents, "config_opts['root'] = 'dist-67-16'\n")

    def test_tls_modes(self):
        self.assertEqual(build_tls_config(["http://koji.example/kojihub"]).mode, "plain-http")
        self.assertEqual(build_tls_config(["https://koji.example/kojihub"], insecure=True).mode, "insecure")
        self.assertEqual(build_tls_config(["https://koji.example/kojihub"]).mode, "system-ca")

    def test_generic_profile_rejects_explicit_vm_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(
                slsa_provenance=None,
                runs=1,
                workdir=tmp,
                rpm_name="anothertest-1.0.0-21.al8.x86_64.rpm",
                koji_server="http://koji.example/kojihub",
                koji_topurl="http://koji.example/kojifiles",
                koji_profile="generic-koji",
                koji_profile_file=[],
                koji_ca_cert=None,
                koji_insecure_ssl=False,
                binary_rpm_base_url=None,
                source_rpm_base_url=None,
                executor="vm",
                repo_fallback="none",
                isolation="simple",
            )
            with patch("guanfu.koji_rebuild.command.KojiClient"), patch(
                "guanfu.koji_rebuild.command.resolve_koji_build",
                return_value=_resolution(tag="dist", release="21.al8"),
            ):
                rc = run_koji_rpm_rebuild(args)
            report = json.loads((Path(tmp) / "anothertest-1.0.0-21.al8.x86_64.rpm" / "report.json").read_text())

        self.assertEqual(rc, 3)
        self.assertEqual(report["build_environment"]["koji_profile_id"], "generic-koji")
        self.assertEqual(report["rebuild"]["status"], "unsupported")
        self.assertIn("does not support executor vm", report["rebuild"]["reason"])


if __name__ == "__main__":
    unittest.main()
