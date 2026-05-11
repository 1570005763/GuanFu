import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from guanfu.koji_rebuild.mock_runner import _diagnose_mock_failure, run_rebuild


class DiagnosticsTests(unittest.TestCase):
    def test_detects_buildroot_runtime_crash_from_scriptlet_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            resultdir = Path(tmp)
            (resultdir / "root.log").write_text(
                "warning: %post(p11-kit-trust) scriptlet failed, signal 11\n"
                "Error: Transaction failed\n"
            )

            diagnosis = _diagnose_mock_failure(resultdir)

        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis["category"], "buildroot_runtime_incompatible")
        self.assertIn("VM", diagnosis["suggested_action"])
        self.assertEqual(diagnosis["evidence"][0]["log"], "root.log")

    def test_ignores_regular_rpmbuild_test_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            resultdir = Path(tmp)
            (resultdir / "build.log").write_text(
                "make: *** [Makefile:1298: test] Error 1\n"
                "Bad exit status from /var/tmp/rpm-tmp (%check)\n"
            )

            diagnosis = _diagnose_mock_failure(resultdir)

        self.assertIsNone(diagnosis)

    def test_run_rebuild_attaches_failure_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            resultdir = tmp / "result"

            def fake_run(_cmd):
                (resultdir / "root.log").write_text(
                    "error: %prein(rpm) scriptlet failed, signal 11\n"
                )
                return SimpleNamespace(returncode=30)

            with patch("subprocess.run", side_effect=fake_run):
                result = run_rebuild(tmp / "mock.cfg", tmp / "pkg.src.rpm", resultdir)

        self.assertEqual(result["exit_code"], 30)
        self.assertEqual(
            result["failure_diagnosis"]["category"],
            "buildroot_runtime_incompatible",
        )


if __name__ == "__main__":
    unittest.main()
