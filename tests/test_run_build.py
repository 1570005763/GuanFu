import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import run_build  # noqa: E402


class RunBuildPayloadTests(unittest.TestCase):
    def test_default_source_payload_is_gzip9(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = run_build.rpm_source_payload_from_env()

        self.assertEqual(payload, "w9.gzdio")
        self.assertIn("%_source_payload w9.gzdio", run_build.build_rpm_macros_content(payload))
        self.assertEqual(run_build.expected_payload_header(payload), ("gzip", "9"))

    def test_source_payload_can_use_ufdio_escape_hatch(self):
        with patch.dict(os.environ, {"GUANFU_RPM_SOURCE_PAYLOAD": "w.ufdio"}, clear=True):
            payload = run_build.rpm_source_payload_from_env()

        self.assertEqual(payload, "w.ufdio")
        self.assertIn("%_source_payload w.ufdio", run_build.build_rpm_macros_content(payload))
        self.assertEqual(run_build.expected_payload_header(payload), ("(none)", ""))

    def test_source_payload_can_use_system_default(self):
        with patch.dict(os.environ, {"GUANFU_RPM_SOURCE_PAYLOAD": "system-default"}, clear=True):
            payload = run_build.rpm_source_payload_from_env()

        self.assertEqual(payload, "system-default")
        self.assertNotIn("%_source_payload", run_build.build_rpm_macros_content(payload))
        self.assertIsNone(run_build.expected_payload_header(payload))

    def test_invalid_source_payload_fails(self):
        with self.assertRaises(run_build.BuildRunnerError):
            run_build.validate_rpm_source_payload("gzip-9")

    def test_ufdio_rejects_compression_flags(self):
        with self.assertRaises(run_build.BuildRunnerError):
            run_build.validate_rpm_source_payload("w9.ufdio")

    def test_verify_source_payload_accepts_matching_header(self):
        spec = {"outputs": [{"path": "/tmp/pkg.src.rpm"}]}
        with patch.object(run_build, "query_source_rpm_payload_header", return_value=("gzip", "9")):
            run_build.verify_source_payload_outputs(spec, "w9.gzdio")

    def test_verify_source_payload_rejects_mismatched_header(self):
        spec = {"outputs": [{"path": "/tmp/pkg.src.rpm"}]}
        with patch.object(run_build, "query_source_rpm_payload_header", return_value=("(none)", "")):
            with self.assertRaises(run_build.BuildRunnerError):
                run_build.verify_source_payload_outputs(spec, "w9.gzdio")

    def test_verify_source_payload_skips_strong_check_for_system_default(self):
        spec = {"outputs": [{"path": "/tmp/pkg.src.rpm"}]}
        with patch.object(run_build, "query_source_rpm_payload_header", return_value=("xz", "2")):
            run_build.verify_source_payload_outputs(spec, "system-default")

    def test_declared_versioned_packages_collects_system_packages_and_tools(self):
        spec = {
            "environment": {
                "systemPackages": [{"name": "rpm-build", "version": "4.14.3-32.al8"}],
                "tools": [{"name": "clang", "version": "15.0.7-1.al8"}],
            }
        }

        self.assertEqual(
            run_build.declared_versioned_packages(spec),
            [("rpm-build", "4.14.3-32.al8"), ("clang", "15.0.7-1.al8")],
        )

    def test_declared_package_version_mismatch_fails(self):
        with patch.object(run_build.shutil, "which", return_value="/usr/bin/rpm"):
            with patch.object(run_build, "query_installed_rpm_versions", return_value=["1.0-1"]):
                with self.assertRaises(run_build.BuildRunnerError):
                    run_build.verify_declared_package_versions([("zlib", "1.0-2")])

    def test_declared_non_toolchain_package_versions_are_not_strictly_verified(self):
        with patch.object(run_build.shutil, "which", return_value="/usr/bin/rpm"):
            with patch.object(run_build, "query_installed_rpm_versions") as query:
                run_build.verify_declared_package_versions([("openssl-devel", "1.1.1k")])

        query.assert_not_called()

    def test_normalize_rpm_version_strips_absent_epoch(self):
        self.assertEqual(run_build._normalize_rpm_version_line("(none):1.2.11-20.al8"), "1.2.11-20.al8")
        self.assertEqual(run_build._normalize_rpm_version_line("0:1.2.11-20.al8"), "1.2.11-20.al8")
        self.assertEqual(run_build._normalize_rpm_version_line("2:1.30-11.al8"), "2:1.30-11.al8")


if __name__ == "__main__":
    unittest.main()
