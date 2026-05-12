#!/usr/bin/env python3
import argparse
import os
import sys

from guanfu import __version__
from guanfu.buildspec_rebuild import run_buildspec_rebuild
from guanfu.koji_rebuild.command import run_koji_rpm_rebuild


def build_parser():
    parser = argparse.ArgumentParser(prog="guanfu")
    parser.add_argument("--version", action="version", version=f"guanfu {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    rebuild = subparsers.add_parser("rebuild", help="Run local rebuild workflows")
    rebuild_subparsers = rebuild.add_subparsers(dest="rebuild_command")

    buildspec = rebuild_subparsers.add_parser(
        "buildspec",
        help="Run the existing buildspec container rebuild flow",
    )
    buildspec.add_argument(
        "--spec",
        default="buildspec.yaml",
        help="Path to buildspec YAML file",
    )
    buildspec.set_defaults(func=run_buildspec_rebuild)

    koji = rebuild_subparsers.add_parser(
        "koji-rpm",
        help="Rebuild an RPM produced by a Koji instance",
        description=(
            "Rebuild an RPM produced by a Koji instance. The default executor runs "
            "mock inside a Linux VM with a controlled CPU model, kernel, and tool "
            "surface close to the Koji builder. KVM is trusted; TCG fallback is degraded."
        ),
        epilog=(
            "VM guidance: --executor vm currently supports an23. By default GuanFu "
            "downloads the OpenAnolis 23.4 x86_64 qcow2 image from the GA mirror and "
            "runs it with QEMU/KVM. If /dev/kvm is absent, GuanFu automatically falls "
            "back to slow degraded QEMU TCG unless --vm-require-kvm is set."
        ),
    )
    source = koji.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--rpm-name",
        help="Published RPM filename, for example zlib-1.2.13-3.an23.x86_64.rpm",
    )
    source.add_argument(
        "--slsa-provenance",
        help="RPM SLSA provenance file. Reserved for the second implementation phase.",
    )
    koji.add_argument(
        "--koji-server",
        default="https://build.openanolis.cn/kojihub",
        help="Koji XML-RPC hub URL",
    )
    koji.add_argument(
        "--koji-topurl",
        default="https://build.openanolis.cn/kojifiles",
        help="Koji topurl for repos and mock-config",
    )
    koji.add_argument(
        "--binary-rpm-base-url",
        default="https://mirrors.openanolis.cn/anolis/23/os/x86_64/os/Packages/",
        help="Base URL for published binary RPMs",
    )
    koji.add_argument(
        "--source-rpm-base-url",
        default="https://mirrors.openanolis.cn/anolis/23/os/source/Packages/",
        help="Base URL for published source RPMs",
    )
    koji.add_argument(
        "--workdir",
        default="guanfu-koji-rebuild",
        help="Directory for downloaded inputs, mock config, rebuild results, and reports",
    )
    koji.add_argument(
        "--executor",
        choices=("vm", "local"),
        default="vm",
        help=(
            "Rebuild executor. vm is the default path; local keeps the host mock flow "
            "for compatibility and diagnostics."
        ),
    )
    koji.add_argument(
        "--vm-image",
        default=os.environ.get("GUANFU_VM_IMAGE"),
        help=(
            "VM image path or URL used by --executor vm. Defaults to the OpenAnolis "
            "23.4 x86_64 GA qcow2 image. Can also be set with GUANFU_VM_IMAGE."
        ),
    )
    koji.add_argument(
        "--vm-image-format",
        default=os.environ.get("GUANFU_VM_IMAGE_FORMAT", "auto"),
        choices=("auto", "qcow2", "raw"),
        help="VM image format. Defaults to auto-detection.",
    )
    koji.add_argument(
        "--vm-kernel",
        default=os.environ.get("GUANFU_VM_KERNEL"),
        help="Optional kernel image for raw direct-init VM boot. Can also be set with GUANFU_VM_KERNEL.",
    )
    koji.add_argument(
        "--vm-initrd",
        default=os.environ.get("GUANFU_VM_INITRD"),
        help="Optional initramfs image for raw direct-init VM boot. Can also be set with GUANFU_VM_INITRD.",
    )
    koji.add_argument(
        "--vm-root-device",
        default="/dev/vda",
        help="Root block device inside the VM.",
    )
    koji.add_argument(
        "--vm-qemu-binary",
        default=os.environ.get("GUANFU_QEMU_BINARY"),
        help="QEMU binary path. Defaults to qemu-system-x86_64, qemu-kvm, or /usr/libexec/qemu-kvm.",
    )
    koji.add_argument(
        "--vm-qemu-img-binary",
        default=os.environ.get("GUANFU_QEMU_IMG_BINARY"),
        help="qemu-img binary path. Defaults to qemu-img.",
    )
    koji.add_argument(
        "--vm-virt-customize-binary",
        default=os.environ.get("GUANFU_VIRT_CUSTOMIZE_BINARY"),
        help="virt-customize binary path used to inject the rebuild service into qcow2 images.",
    )
    koji.add_argument(
        "--vm-share-mode",
        default=os.environ.get("GUANFU_VM_SHARE_MODE", "auto"),
        choices=("auto", "9p", "image-copy"),
        help=(
            "How host inputs/results are exchanged with the VM. auto uses 9p when QEMU "
            "supports it, otherwise copies inputs into the qcow2 overlay and copies "
            "results back after shutdown."
        ),
    )
    koji.add_argument(
        "--vm-prepare-packages",
        default=os.environ.get("GUANFU_VM_PREPARE_PACKAGES", "mock,rpm-build"),
        help=(
            "Comma-separated packages installed into the qcow2 overlay before boot. "
            "Set to an empty string to skip. Defaults to mock,rpm-build."
        ),
    )
    koji.add_argument(
        "--vm-virt-copy-in-binary",
        default=os.environ.get("GUANFU_VIRT_COPY_IN_BINARY"),
        help="virt-copy-in binary path used by --vm-share-mode image-copy.",
    )
    koji.add_argument(
        "--vm-virt-copy-out-binary",
        default=os.environ.get("GUANFU_VIRT_COPY_OUT_BINARY"),
        help="virt-copy-out binary path used by --vm-share-mode image-copy.",
    )
    koji.add_argument(
        "--vm-cpu",
        default=os.environ.get("GUANFU_VM_CPU", "Cascadelake-Server-v1"),
        help="QEMU CPU model used by --executor vm.",
    )
    koji.add_argument(
        "--vm-machine",
        default="q35",
        help="QEMU machine type used by --executor vm.",
    )
    koji.add_argument(
        "--vm-memory",
        default=os.environ.get("GUANFU_VM_MEMORY", "4096M"),
        help="Memory size passed to QEMU.",
    )
    koji.add_argument(
        "--vm-smp",
        default=os.environ.get("GUANFU_VM_SMP", "2"),
        help="vCPU count passed to QEMU.",
    )
    koji.add_argument(
        "--vm-timeout",
        type=int,
        default=int(os.environ.get("GUANFU_VM_TIMEOUT", "7200")),
        help="VM execution timeout in seconds.",
    )
    koji.add_argument(
        "--vm-workdir",
        default="/mnt/guanfu-work",
        help="Path where the GuanFu run directory is mounted inside the VM.",
    )
    koji.add_argument(
        "--vm-require-kvm",
        action="store_true",
        default=os.environ.get("GUANFU_VM_REQUIRE_KVM", "").lower() in ("1", "true", "yes", "on"),
        help="Fail if /dev/kvm is unavailable instead of automatically falling back to TCG.",
    )
    koji.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of rebuild runs",
    )
    koji.add_argument(
        "--isolation",
        default="simple",
        help="mock isolation mode",
    )
    koji.add_argument(
        "--repo-fallback",
        choices=("installed-pkgs", "none"),
        default="installed-pkgs",
        help=(
            "Fallback strategy when the original Koji buildroot repo is unavailable. "
            "installed-pkgs reconstructs a temporary local repo from installed_pkgs.log, "
            "Koji task outputs, and event-time external repos, then disables mock bootstrap."
        ),
    )
    koji.set_defaults(func=run_koji_rpm_rebuild)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
