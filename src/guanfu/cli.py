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
