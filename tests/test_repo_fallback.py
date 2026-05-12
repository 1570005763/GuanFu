import tempfile
import unittest
from pathlib import Path

from guanfu.koji_rebuild.repo_fallback import (
    _packages_from_install_command,
    _replace_repo_arch,
    parse_installed_pkgs,
    rewrite_mock_config_for_local_repo,
    summarize_fallback_report,
)


class RepoFallbackTests(unittest.TestCase):
    def test_parse_installed_pkgs_strips_epoch_for_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed_pkgs.log"
            path.write_text(
                "fonts-filesystem-1:4.0.2-4.an23.noarch 1681297983 0 "
                "caaa42e89bc6a7f73211a3bcc9c85783 installed\n"
            )

            entries = parse_installed_pkgs(path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["nevra"], "fonts-filesystem-1:4.0.2-4.an23.noarch")
        self.assertEqual(entries[0]["rpm_lookup"], "fonts-filesystem-4.0.2-4.an23.noarch")
        self.assertEqual(entries[0]["payloadhash"], "caaa42e89bc6a7f73211a3bcc9c85783")

    def test_rewrite_mock_config_replaces_repo_baseurl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "mock.cfg"
            dest = tmp / "mock-fallback.cfg"
            repo = tmp / "repo"
            repo.mkdir()
            source.write_text(
                "config_opts['yum.conf'] = '[main]\\n"
                "[build]\\n"
                "name=build\\n"
                "baseurl=https://build.openanolis.cn/kojifiles/repos/dist/1/x86_64\\n'\n"
            )

            rewrite_mock_config_for_local_repo(source, dest, repo)

            rewritten = dest.read_text()
        self.assertIn("baseurl=file://", rewritten)
        self.assertIn(str(repo), rewritten)
        self.assertNotIn("https://build.openanolis.cn/kojifiles/repos", rewritten)

    def test_rewrite_mock_config_can_add_releasever(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "mock.cfg"
            dest = tmp / "mock-fallback.cfg"
            repo = tmp / "repo"
            repo.mkdir()
            source.write_text(
                "config_opts['yum.conf'] = '[main]\\n"
                "[build]\\n"
                "name=build\\n"
                "baseurl=https://build.openanolis.cn/kojifiles/repos/dist/1/x86_64\\n'\n"
            )

            rewrite_mock_config_for_local_repo(source, dest, repo, releasever="23")

            self.assertIn("config_opts['releasever'] = '23'", dest.read_text())

    def test_rewrite_mock_config_can_disable_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "mock.cfg"
            dest = tmp / "mock-fallback.cfg"
            repo = tmp / "repo"
            repo.mkdir()
            source.write_text(
                "config_opts['use_bootstrap'] = True\n"
                "config_opts['use_bootstrap_image'] = True\n"
                "config_opts['yum.conf'] = '[main]\\n"
                "[build]\\n"
                "name=build\\n"
                "baseurl=https://build.openanolis.cn/kojifiles/repos/dist/1/x86_64\\n'\n"
            )

            rewrite_mock_config_for_local_repo(source, dest, repo, disable_bootstrap=True)

            rewritten = dest.read_text()
        self.assertIn("config_opts['use_bootstrap'] = False", rewritten)
        self.assertIn("config_opts['use_bootstrap_image'] = False", rewritten)

    def test_external_repo_url_replaces_arch_tokens(self):
        self.assertEqual(
            _replace_repo_arch("https://example.invalid/$arch/os/${arch}/$basearch/", "x86_64"),
            "https://example.invalid/x86_64/os/x86_64/x86_64/",
        )

    def test_packages_from_install_command_skips_command_and_options(self):
        self.assertEqual(
            _packages_from_install_command("install --setopt=a=b python3-dnf python3-dnf-plugins-core"),
            ["python3-dnf", "python3-dnf-plugins-core"],
        )

    def test_summarize_fallback_report_omits_local_paths(self):
        summary = summarize_fallback_report(
            {
                "strategy": "installed_pkgs_log",
                "status": "incomplete",
                "source_task_id": 123,
                "installed_pkgs_log": {
                    "file": "installed_pkgs.log",
                    "path": "/tmp/run/inputs/installed_pkgs.log",
                    "size": 10,
                    "sha256": "abc",
                },
                "dependency_recovery": {
                    "total": 1,
                    "resolved_by_getRPM": 0,
                    "unresolved": 1,
                    "payloadhash_mismatch": 0,
                    "task_output_available": 0,
                    "missing_task_output": 0,
                    "downloaded": 0,
                    "download_errors": 0,
                },
            }
        )

        self.assertNotIn("path", summary["installed_pkgs_log"])

    def test_summarize_fallback_report_includes_external_and_bootstrap(self):
        summary = summarize_fallback_report(
            {
                "strategy": "installed_pkgs_log",
                "status": "ready",
                "dependency_recovery": {
                    "total": 2,
                    "resolved_by_getRPM": 1,
                    "resolved_by_external_repo": 1,
                    "unresolved": 0,
                    "payloadhash_mismatch": 0,
                    "task_output_available": 1,
                    "missing_task_output": 0,
                    "downloaded": 2,
                    "download_errors": 0,
                },
                "external_repo_recovery": {
                    "status": "ready",
                    "event_id": 123,
                    "repos": [{"external_repo_name": "fc36"}],
                    "resolved": [
                        {
                            "nevra": "kernel-headers-5.17.0-300.fc36.x86_64",
                            "artifact": {
                                "file": "kernel-headers.rpm",
                                "path": "/tmp/private/kernel-headers.rpm",
                                "sha256": "abc",
                            },
                        }
                    ],
                },
                "bootstrap_toolchain": {
                    "status": "disabled",
                    "package_manager": "dnf4",
                    "requested_packages": ["python3-dnf"],
                    "downloaded": None,
                    "historical_exactness": "not_applicable",
                    "use_bootstrap": False,
                    "use_bootstrap_image": False,
                    "original_use_bootstrap": True,
                    "original_use_bootstrap_image": True,
                    "reason": "installed_pkgs fallback disables mock bootstrap",
                },
            }
        )

        self.assertEqual(summary["dependency_recovery"]["resolved_by_external_repo"], 1)
        self.assertEqual(summary["external_repo_recovery"]["resolved"], 1)
        self.assertNotIn("path", summary["external_repo_recovery"]["resolved_items"][0]["artifact"])
        self.assertEqual(summary["bootstrap_toolchain"]["status"], "disabled")
        self.assertFalse(summary["bootstrap_toolchain"]["use_bootstrap"])
        self.assertTrue(summary["bootstrap_toolchain"]["original_use_bootstrap"])


if __name__ == "__main__":
    unittest.main()
