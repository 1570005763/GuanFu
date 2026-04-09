# GuanFu

A GitHub Action for **reproducible container-based builds**. GuanFu reads a declarative `buildspec.yaml`, launches a container, and executes build phases inside it — producing deterministic, auditable artifacts.

## Features

- **Declarative build specs** — Define container image, inputs, environment, build phases, and outputs in a single YAML file.
- **Container isolation** — All builds run inside Docker containers for consistency across environments.
- **Reproducible builds** — Pre-configured `SOURCE_DATE_EPOCH`, RPM macros, and Rust compiler flags for bit-for-bit reproducibility.
- **Input management** — Download remote artifacts or mount local files into the build container, with optional SHA-256 verification.
- **Multi-OS support** — Built-in runners for Anolis OS and Alibaba Cloud Linux, extensible to other distributions.
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
├── src/
│   ├── build-runner.sh         # Entry point — sets up Docker and mounts
│   ├── run_build.py            # Main build orchestrator (runs inside container)
│   ├── validate_buildspec.py   # Validates buildspec paths
│   └── os_runners/             # OS-specific package installation
│       ├── base_runner.py
│       ├── anolis_runner.py
│       └── alinux_runner.py
├── .github/workflows/
│   └── release.yml             # Reusable release + SLSA provenance workflow
└── docs/
    ├── buildspec.md            # Buildspec YAML specification
    ├── local_rebuild_guide.md  # Local build guide
    └── workflow_usage_guide.md # CI workflow usage guide
```

## Supported Operating Systems

| OS                  | Status    |
|---------------------|-----------|
| Anolis OS           | Supported |
| Alibaba Cloud Linux | Supported |
| Others              | Extensible via `OsRunnerBase` |

## License

This project is licensed under the [Apache License 2.0](LICENSE).
