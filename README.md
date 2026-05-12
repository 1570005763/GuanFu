# GuanFu

GuanFu is a reproducible rebuild toolkit. It supports the original declarative
`buildspec.yaml` container workflow, and also provides a local CLI path for
rebuilding published OpenAnolis Koji RPMs inside a controlled VM executor.

## Features

- **Declarative build specs** — Define container image, inputs, environment, build phases, and outputs in a single YAML file.
- **Buildspec container isolation** — Declarative buildspec rebuilds run inside Docker containers for consistency across environments.
- **Reproducible builds** — Pre-configured `SOURCE_DATE_EPOCH`, RPM macros, and Rust compiler flags for bit-for-bit reproducibility.
- **Input management** — Download remote artifacts or mount local files into the build container, with optional SHA-256 verification.
- **Multi-OS support** — Built-in runners for Anolis OS and Alibaba Cloud Linux, extensible to other distributions.
- **Local CLI** — Use `guanfu` to run the existing buildspec rebuild flow or rebuild published OpenAnolis Koji RPMs locally.
- **Koji RPM VM rebuild** — Resolve published RPMs back to Koji build metadata, SRPMs, mock configs, and logs; rebuild them in an an23 VM and compare against release RPMs.
- **Release workflow** — Reusable GitHub Actions workflow with SLSA provenance generation and optional Rekor transparency log upload.

## Quick Start

### As a GitHub Action

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: <owner>/GuanFu@v1
        with:
          spec_path: buildspec.yaml
```

### Buildspec Example

Create a `buildspec.yaml` in your repository:

```yaml
version: 1

container:
  image: "registry.example.com/build-base:anolis23"

inputs:
  vendor:
    url: "https://example.com/vendor.tar.gz"
    sha256: "abcdef1234..."
    targetPath: "/workspace/vendor.tar.gz"

environment:
  variables:
    - name: LANG
      value: C.UTF-8
  systemPackages:
    - name: openssl-devel
      version: "1.1.1k"
  tools:
    - name: rust
      version: "1.72.0"

phases:
  prepare:
    commands:
      - tar xzf /workspace/vendor.tar.gz -C /workspace
  build:
    commands:
      - cargo build --release --locked

outputs:
  - path: "/workspace/target/release/my-service"
    sha256: "a1b2c3d4..."
```

See [docs/buildspec.md](docs/buildspec.md) for the full specification.

### Local CLI

From a source checkout:

```bash
python3 -m pip install -e .
```

Run the existing buildspec rebuild flow through the unified CLI:

```bash
guanfu rebuild buildspec --spec buildspec.yaml
```

The legacy script entry point remains supported:

```bash
./src/build-runner.sh buildspec.yaml
```

Rebuild a published OpenAnolis Koji RPM locally:

```bash
guanfu rebuild koji-rpm \
  --rpm-name zlib-1.2.13-3.an23.x86_64.rpm
```

Koji RPM rebuild currently supports an23 RPMs and requires a Linux host with
`koji` plus QEMU/libguestfs tooling. `createrepo_c` is required when GuanFu must
reconstruct a temporary repo from `installed_pkgs.log`.
The default Koji executor is `--executor vm`; it uses KVM when `/dev/kvm` is
available and otherwise falls back to slow degraded QEMU TCG. Use
`--vm-require-kvm` for strict trusted runs. The default an23 VM image is:
`https://mirrors.openanolis.cn/anolis/23/isos/GA/x86_64/AnolisOS-23.4-x86_64.qcow2`.
GuanFu checks host VM dependencies and reports install hints instead of
installing system packages automatically. The host needs `qemu-img` and
`virt-customize` to prepare the VM overlay. GuanFu uses QEMU 9p sharing when
available, and falls back to `virt-copy-in` / `virt-copy-out` when the host QEMU
lacks 9p support. By default the qcow2 overlay is prepared with `mock,rpm-build`
before boot. `--executor local` remains available for compatibility and host
mock diagnostics, but it shares the host kernel/CPU boundary and is not the
default trusted route.

## Action Inputs

| Input       | Description               | Required | Default          |
|-------------|---------------------------|----------|------------------|
| `spec_path` | Path to buildspec YAML    | No       | `buildspec.yaml` |

## Release Workflow

GuanFu includes a reusable release workflow (`.github/workflows/release.yml`) that supports:

- Uploading build input/output artifacts to GitHub Releases
- Generating SLSA v1 provenance attestations
- Optional RPM binary-level provenance
- Optional upload of provenance to Rekor transparency log

```yaml
jobs:
  release:
    uses: <owner>/GuanFu/.github/workflows/release.yml@main
    with:
      release_tag_name: v1.0.0
      input_artifact: build-inputs
      output_artifact: build-outputs
      release_slsa_provenance: true
```

See [docs/workflow_usage_guide.md](docs/workflow_usage_guide.md) for details.

## Project Structure

```
├── action.yml                  # GitHub Action definition
├── pyproject.toml              # Python package and guanfu console script
├── src/
│   ├── build-runner.sh         # Entry point — sets up Docker and mounts
│   ├── guanfu/                 # Unified local CLI and Koji RPM rebuild modules
│   ├── run_build.py            # Main build orchestrator (runs inside container)
│   ├── validate_buildspec.py   # Validates buildspec paths
│   └── os_runners/             # OS-specific package installation
│       ├── base_runner.py
│       ├── anolis_runner.py
│       └── alinux_runner.py
├── .github/workflows/
│   └── release.yml             # Reusable release + SLSA provenance workflow
└── docs/
    ├── buildspec.md                         # Buildspec YAML specification
    ├── local_rebuild_guide.md               # Local build and Koji RPM rebuild guide
    ├── koji_bootstrap_toolchain_fallback.md # Future strict bootstrap fallback design
    └── workflow_usage_guide.md              # CI workflow usage guide
```

## Supported Operating Systems

For the buildspec container workflow:

| OS                  | Status    |
|---------------------|-----------|
| Anolis OS           | Supported |
| Alibaba Cloud Linux | Supported |
| Others              | Extensible via `OsRunnerBase` |

For Koji RPM VM rebuild, this PR currently supports OpenAnolis an23 RPMs. Other
targets such as an8, alinux3, and alinux4 are intentionally left to future
policy support.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
