import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from guanfu.koji_rebuild.vm_executor import (
    DEFAULT_AN23_VM_IMAGE_URL,
    _select_acceleration,
    build_qemu_command,
    detect_target_os,
    parse_koji_recorded_environment,
    prepare_vm_image,
    prepare_vm_mock_config,
    run_vm_rebuild,
    vm_executor_summary,
)


class VmExecutorTests(unittest.TestCase):
    def test_detect_target_os_from_buildroot_tag(self):
        self.assertEqual(
            detect_target_os(buildroot={"tag_name": "dist-an23.0-build"}),
            "an23",
        )

    def test_unknown_target_is_not_supported(self):
        self.assertIsNone(
            detect_target_os(
                rpm_info={"release": "1.an8"},
                buildroot={"tag_name": "dist-an8-build"},
            )
        )

    def test_prepare_vm_mock_config_rewrites_local_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp).resolve()
            source = run_dir / "inputs" / "mock.cfg"
            dest = run_dir / "inputs" / "mock-vm.cfg"
            source.parent.mkdir()
            source.write_text("baseurl=file://%s/fallback-repo\n" % run_dir)

            prepare_vm_mock_config(source, dest, run_dir, "/mnt/guanfu-work")

            self.assertEqual(dest.read_text(), "baseurl=file:///mnt/guanfu-work/fallback-repo\n")

    def test_build_qemu_command_mounts_run_dir_with_virtfs(self):
        args = SimpleNamespace(
            vm_machine="q35",
            vm_smp=2,
            vm_memory="4096M",
            vm_image="/tmp/an23.raw",
            vm_image_format="raw",
            vm_kernel="/boot/vmlinuz",
            vm_initrd="/boot/initramfs.img",
            vm_root_device="/dev/vda",
        )
        command = build_qemu_command(
            args,
            qemu_binary="/usr/bin/qemu-system-x86_64",
            profile={"qemu_cpu": "Cascadelake-Server-v1"},
            acceleration="kvm",
            shared_dir=Path("/tmp/run"),
        )

        self.assertEqual(command[:4], ["/usr/bin/qemu-system-x86_64", "-accel", "kvm", "-machine"])
        self.assertIn("-virtfs", command)
        self.assertIn("mount_tag=guanfu_work", command[command.index("-virtfs") + 1])
        self.assertIn("Cascadelake-Server-v1", command)
        self.assertIn("-kernel", command)

    def test_acceleration_auto_falls_back_to_tcg(self):
        with patch("guanfu.koji_rebuild.vm_executor._kvm_available", return_value=False):
            acceleration, warning = _select_acceleration(require_kvm=False)

        self.assertEqual(acceleration, "tcg")
        self.assertIn("falling back", warning)

    def test_acceleration_can_require_kvm(self):
        with patch("guanfu.koji_rebuild.vm_executor._kvm_available", return_value=False):
            with self.assertRaises(RuntimeError):
                _select_acceleration(require_kvm=True)

    def test_build_qemu_command_can_skip_virtfs_for_image_copy(self):
        args = SimpleNamespace(
            vm_machine="q35",
            vm_smp=2,
            vm_memory="4096M",
            vm_image="/tmp/an23.qcow2",
            vm_image_format="qcow2",
        )
        command = build_qemu_command(
            args,
            qemu_binary="/usr/bin/qemu-system-x86_64",
            profile={"qemu_cpu": "Cascadelake-Server-v1"},
            acceleration="tcg",
            shared_dir=Path("/tmp/run"),
            share_mode="image-copy",
        )

        self.assertNotIn("-virtfs", command)
        self.assertIn("-drive", command)

    def test_prepare_vm_image_downloads_default_qcow2_to_cache_and_creates_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp).resolve() / "pkg"
            run_dir.mkdir()
            (run_dir / "metadata").mkdir()
            args = SimpleNamespace(
                vm_image=None,
                vm_image_format="auto",
                vm_qemu_img_binary="/bin/echo",
            )

            def fake_download(_url, dest):
                dest.parent.mkdir(parents=True)
                dest.write_bytes(b"qcow2")
                return dest

            with patch("guanfu.koji_rebuild.vm_executor.download_url", side_effect=fake_download), patch(
                "subprocess.run"
            ) as run:
                image = prepare_vm_image(
                    args,
                    run_dir,
                    {
                        "default_image_url": DEFAULT_AN23_VM_IMAGE_URL,
                        "default_image_format": "qcow2",
                    },
                )

        self.assertEqual(image["format"], "qcow2")
        self.assertEqual(image["source"]["url"], DEFAULT_AN23_VM_IMAGE_URL)
        self.assertTrue(image["path"].endswith("vm-overlay.qcow2"))
        run.assert_called_once()

    def test_vm_summary_marks_partial_cpu_match_and_degraded_tcg(self):
        summary = vm_executor_summary(
            profile={
                "name": "an23-koji-cascadelake",
                "target_os": "an23",
                "qemu_cpu": "Cascadelake-Server-v1",
                "expected_cpu_family": "6",
                "expected_cpu_model_id": "85",
                "expected_kernel": "4.18.0-193.28.1.el8_2.x86_64",
                "expected_mock": "mock-2.12-1.el8",
            },
            acceleration="tcg",
            koji_recorded={"cpu_family": "6", "cpu_model_id": "85", "kernel": "4.18.0-193.28.1.el8_2.x86_64"},
            actual_vm={"cpu_family": "6", "cpu_model_id": "85", "kernel": "5.10.134", "mock": "5.5"},
        )

        self.assertEqual(summary["environment_match"]["cpu"], "partial")
        self.assertEqual(summary["environment_match"]["kernel"], "mismatch")
        self.assertEqual(summary["trust_environment"], "degraded")

    def test_parse_koji_recorded_environment_from_logs(self):
        class Resolution:
            buildroot = {"host_id": 6, "host_name": "anolis-x86_64-builder-03", "container_type": "chroot"}

        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / "hw_info.log").write_text(
                "Model name:          Intel(R) Xeon(R) Platinum 8269CY CPU T 3.10GHz\n"
                "CPU family:          6\n"
                "Model:               85\n"
                "Flags:               fpu sse avx512vl\n"
            )
            (inputs / "root.log").write_text("DEBUG buildroot.py:675:  kernel version == 4.18.0-193.28.1.el8_2.x86_64\n")
            (inputs / "mock_output.log").write_text("INFO: mock.py version 2.12 starting (python version = 3.6.8, NVR = mock-2.12-1.el8)...\n")

            env = parse_koji_recorded_environment(Resolution(), inputs)

        self.assertEqual(env["host_name"], "anolis-x86_64-builder-03")
        self.assertEqual(env["cpu_model_id"], "85")
        self.assertEqual(env["kernel"], "4.18.0-193.28.1.el8_2.x86_64")
        self.assertEqual(env["mock"], "mock-2.12-1.el8")
        self.assertIn("avx512vl", env["cpu_flags"])

    def test_run_vm_rebuild_collects_mock_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp).resolve()
            inputs = run_dir / "inputs"
            results = run_dir / "results"
            inputs.mkdir()
            (run_dir / "metadata").mkdir()
            mock_cfg = inputs / "mock.cfg"
            srpm = inputs / "pkg.src.rpm"
            mock_cfg.write_text("baseurl=file://%s/fallback-repo\n" % run_dir)
            srpm.write_text("srpm")
            image = run_dir / "vm.raw"
            kernel = run_dir / "vmlinuz"
            initrd = run_dir / "initrd.img"
            for path in (image, kernel, initrd):
                path.write_text("x")
            args = SimpleNamespace(
                vm_image=str(image),
                vm_image_format="raw",
                vm_kernel=str(kernel),
                vm_initrd=str(initrd),
                vm_root_device="/dev/vda",
                vm_qemu_binary=str(kernel),
                vm_cpu="Cascadelake-Server-v1",
                vm_machine="q35",
                vm_smp=2,
                vm_memory="1024M",
                vm_timeout=60,
                vm_workdir="/mnt/guanfu-work",
                vm_share_mode="9p",
                runs=1,
                isolation="simple",
            )

            def fake_run(_command, timeout=None):
                resultdir = results / "result-run-1"
                resultdir.mkdir(parents=True)
                (resultdir / "mock.exit").write_text("0")
                (resultdir / "pkg.x86_64.rpm").write_bytes(b"rpm")
                (run_dir / "metadata" / "vm-rebuild.log").write_text(
                    "VM_ACTUAL_KERNEL=5.10\n"
                    "VM_ACTUAL_MOCK=5.5\n"
                    "VM_ACTUAL_CPU_FAMILY=6\n"
                    "VM_ACTUAL_CPU_MODEL_ID=85\n"
                )
                return SimpleNamespace(returncode=0)

            with patch("guanfu.koji_rebuild.vm_executor._inject_vm_script_raw"), patch(
                "subprocess.run", side_effect=fake_run
            ):
                result = run_vm_rebuild(args, run_dir, mock_cfg, srpm, results, "an23")

        self.assertEqual(result["rebuilds"][0]["exit_code"], 0)
        self.assertEqual(result["executor"]["mode"], "vm")
        self.assertEqual(result["executor"]["actual_vm"]["kernel"], "5.10")
        self.assertEqual(result["rebuilds"][0]["rpms"][0]["file"], "pkg.x86_64.rpm")


if __name__ == "__main__":
    unittest.main()
