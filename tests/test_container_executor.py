import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from guanfu.koji_rebuild.container_executor import (
    CONTAINER_WORKDIR,
    build_container_command,
    detect_target_os,
)


class ContainerExecutorTests(unittest.TestCase):
    def test_detect_target_os_from_buildroot_tag(self):
        self.assertEqual(
            detect_target_os(buildroot={"tag_name": "dist-an23.0-build"}),
            "an23",
        )

    def test_detect_target_os_from_rpm_release(self):
        self.assertEqual(
            detect_target_os(rpm_info={"release": "3.an23"}),
            "an23",
        )

    def test_unknown_target_is_not_supported(self):
        self.assertIsNone(
            detect_target_os(
                rpm_info={"release": "1.an8"},
                buildroot={"tag_name": "dist-an8-build"},
            )
        )

    def test_container_command_reexecs_with_local_executor(self):
        args = SimpleNamespace(
            rpm_name="acl-2.3.1-3.an23.x86_64.rpm",
            koji_server="https://build.openanolis.cn/kojihub",
            koji_topurl="https://build.openanolis.cn/kojifiles",
            binary_rpm_base_url="https://mirrors.openanolis.cn/anolis/23/os/x86_64/os/Packages/",
            source_rpm_base_url="https://mirrors.openanolis.cn/anolis/23/os/source/Packages/",
            runs=1,
            isolation="simple",
            repo_fallback="installed-pkgs",
            container_privileged=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            expected_mount = f"{Path(tmp).resolve()}:{CONTAINER_WORKDIR}"
            command = build_container_command(args, "docker", "example/an23:latest", Path(tmp))

        self.assertEqual(command[:4], ["docker", "run", "--rm", "--privileged"])
        self.assertIn(expected_mount, command)
        self.assertIn("GUANFU_IN_CONTAINER=1", command)
        self.assertIn("GUANFU_TARGET_OS=an23", command)
        self.assertIn("example/an23:latest", command)

        inner = command[command.index("guanfu") :]
        self.assertEqual(inner[:3], ["guanfu", "rebuild", "koji-rpm"])
        self.assertIn("--executor", inner)
        self.assertEqual(inner[inner.index("--executor") + 1], "local")
        self.assertEqual(inner[inner.index("--workdir") + 1], CONTAINER_WORKDIR)


if __name__ == "__main__":
    unittest.main()
