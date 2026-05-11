#!/usr/bin/env python3
import argparse
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
            "Rebuild an RPM produced by a Koji instance. The default executor runs mock "
            "inside a container. If an old buildroot crashes while executing RPM scriptlets "
            "or buildroot tools, rerun GuanFu inside a Linux VM with a controlled CPU model; "
            "a VM can solve host/container CPU or kernel exposure mismatches."
        ),
        epilog=(
            "VM guidance: container and local executors share the host kernel/CPU exposure. "
            "For failures such as scriptlet failed with signal 11, segmentation fault, "
            "illegal instruction, or invalid opcode, use a KVM/QEMU VM close to the Koji "
            "builder environment and run the same guanfu rebuild koji-rpm command inside it."
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
        choices=("container", "local"),
        default="container",
        help=(
            "Rebuild executor. container is the default path; local keeps the existing host "
            "mock flow. If both hit old buildroot runtime crashes, run GuanFu inside a VM."
        ),
    )
    koji.add_argument(
        "--container-runtime",
        choices=("auto", "podman", "docker"),
        default="auto",
        help="Container runtime used by --executor container",
    )
    koji.add_argument(
        "--container-image",
        help="Container image used by --executor container. Defaults to the an23 GuanFu rebuild image.",
    )
    koji.add_argument(
        "--container-privileged",
        dest="container_privileged",
        action="store_true",
        default=True,
        help="Run the rebuild container with --privileged. This is the default.",
    )
    koji.add_argument(
        "--no-container-privileged",
        dest="container_privileged",
        action="store_false",
        help="Do not pass --privileged to the container runtime.",
    )
    koji.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of local rebuild runs",
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
            "Koji task outputs, event-time external repos, and local mock bootstrap tooling."
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
