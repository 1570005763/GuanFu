import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from guanfu.koji_rebuild.mock_runner import run_rebuild


class MockRunnerTests(unittest.TestCase):
    def test_container_resultdir_is_writable_by_nonroot_mockbuild_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            resultdir = tmp / "result"
            with patch.dict(
                os.environ,
                {"GUANFU_EXECUTOR_MODE": "container", "GUANFU_IN_CONTAINER": "1"},
            ), patch("subprocess.run", return_value=SimpleNamespace(returncode=0)):
                run_rebuild(tmp / "mock.cfg", tmp / "pkg.src.rpm", resultdir)

            mode = stat.S_IMODE(resultdir.stat().st_mode)

        self.assertEqual(mode, 0o777)


if __name__ == "__main__":
    unittest.main()
