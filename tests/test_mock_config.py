import tempfile
import unittest
from pathlib import Path

from guanfu.koji_rebuild.mock_config import enforce_nonroot_mock_build_user


class MockConfigTests(unittest.TestCase):
    def test_enforce_nonroot_mock_build_user_replaces_root_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mock.cfg"
            path.write_text(
                "config_opts['chrootuid'] = 0\n"
                "config_opts['chrootgid'] = 135\n"
                "config_opts['root'] = 'dist-an23-build'\n"
            )

            enforce_nonroot_mock_build_user(path)

            text = path.read_text()
        self.assertIn("config_opts['chrootuid'] = 1000", text)
        self.assertIn("config_opts['chrootgid'] = 135", text)
        self.assertNotIn("config_opts['chrootuid'] = 0", text)

    def test_enforce_nonroot_mock_build_user_appends_missing_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mock.cfg"
            path.write_text("config_opts['root'] = 'dist-an23-build'\n")

            enforce_nonroot_mock_build_user(path, uid=2000)

            text = path.read_text()
        self.assertIn("# GuanFu container mock user override.", text)
        self.assertIn("config_opts['chrootuid'] = 2000", text)
        self.assertNotIn("chrootgid", text)


if __name__ == "__main__":
    unittest.main()
