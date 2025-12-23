# .buildspec.yaml 规范（v1）

本规范用于在构建容器中以声明式方式定义：

- 构建容器镜像
- 构建输入（如 vendor 包、子仓库等）
- 构建环境（系统包、工具版本）
- 构建流程（各阶段命令）

配合 `build-runner`（`run_build.py` + `build-runner.sh`），可以在 GitHub Actions 或其他 CI 中统一执行。

---

## 顶层结构

示例：

```yaml
version: 1

container:
  image: "registry.example.com/build-base:anolis23-rust-node"

inputs:
  rustVendor:
    url: "https://artifact.example.com/my-rust-service/releases/v1.2.3/backend-vendor.tar.gz"
    sha256: "12ab34cd..."
    targetDir: "backend/vendor"

environment:
  systemPackages:
    - name: "openssl-devel"
      version: "1.1.1k"
    - name: "zlib-devel"
      version: "1.2.11"
  tools:
    - name: "node"
      version: "18.x"
    - name: "rust"
      version: "1.72.0"

phases:
  prepare:
    commands:
      - ls -R backend/vendor
  build:
    commands:
      - cargo build --release --locked --manifest-path backend/Cargo.toml
```

字段说明：

### `version`

- 当前规范版本，必须为 `1`。

### `container`

```yaml
container:
  image: "registry.example.com/build-base:anolis23-rust-node"
  env:
    KEY: "VALUE" # 预留，当前未在 run_build.py 中使用
```

- `image`：构建所使用的容器镜像名称。
  - 该镜像必须包括：
    - `python3`、`pip`、`curl`、`git`、`tar`、`dnf`/`yum`；
    - `build-runner` 脚本（通常是 `/usr/local/bin/build-runner`）；
    - `run_build.py`（通常是 `/opt/build-system/run_build.py`）。
- `env`：预留字段，目前不在 runner 中使用。

---

## `inputs`：构建输入

`inputs` 用于声明构建所需的外部输入，例如从制品库下载的 vendor 包等。

结构：

```yaml
inputs:
  <name>:
    url: "https://artifact.example.com/my-service/releases/v1.2.3/backend-vendor.tar.gz"
    sha256: "12ab34cd..."    # 可选，若提供则做 sha256 校验
    targetDir: "backend/vendor"
```

字段：

```yaml
inputs:
  rustVendor:
    url: "https://artifact.example.com/my-rust-service/releases/v1.2.3/backend-vendor.tar.gz"
    sha256: "12ab34cd..."    # 可选，若提供则做 sha256 校验
    targetDir: "backend/vendor"
```

- `url`：下载地址。
- `sha256`：可选，若提供，runner 会在解压前校验文件哈希。
- `targetDir`：解压目标目录（相对于工作目录）。

行为：

- Runner 执行：
  - `curl -L <url> -o /tmp/<name>.tar.gz`
  - 如有 `sha256`，则 `echo "<sha256>  /tmp/<name>.tar.gz" | sha256sum -c -`
  - `mkdir -p <targetDir>`
  - `tar xf /tmp/<name>.tar.gz -C <targetDir> --strip-components=1`

---

## `environment`：构建环境

用于声明构建中需要的系统级依赖和工具版本。

```yaml
environment:
  systemPackages:
    - name: "openssl-devel"
      version: "1.1.1k"
    - name: "zlib-devel"
      version: "1.2.11"
  tools:
    - name: "node"
      version: "18.x"
    - name: "rust"
      version: "1.72.0"
```

### `systemPackages`

- 一个对象列表，每个对象包含包的名称和版本。
- 在 AnolisOS 23 下，runner 会调用 `dnf` 或 `yum`：

  ```bash
  dnf install -y openssl-devel-1.1.1k zlib-devel-1.2.11
  # 或
  yum install -y ...
  ```

- 在非 Anolis 23 OS 下，当前版本的 runner 会直接报错（未实现）。

### `tools`

- 一个对象列表，每个对象包含工具的名称和版本。
- 当前支持的工具：
  - `name: "node"`：需要的 Node.js 版本；
  - `name: "rust"`：需要的 Rust 版本。

在 AnolisOS 23 的简单实现中：

- `name: "node"`：
  - 无论具体版本值为何（如 "18.x"），示例 runner 都安装发行版提供的 `nodejs` 包；
  - 具体版本控制可由你未来自行扩展（例如使用 nvm/asdf 或内部安装脚本）。
- `name: "rust"`：
  - 当前示例中，runner 安装发行版 `rust` 和 `cargo` 包；
  - 未来可根据版本字符串决定是否使用 rustup 安装特定版本。

在非 Anolis 23 OS 下：

- 对 `name: "node"` 和 `name: "rust"` 的处理目前均为未实现，将报错退出。

---

## `phases`：构建过程

`phases` 描述构建过程中需要执行的各阶段命令。

```yaml
phases:
  prepare:
    commands:
      - ls -R backend/vendor
  build:
    commands:
      - cargo build --release --locked --manifest-path backend/Cargo.toml
```

支持的阶段名：

- `prepare`
- `build`

每个阶段格式：

```yaml
<phase_name>:
  commands:
    - "<shell command 1>"
    - "<shell command 2>"
    - ...
```

Runner 会按顺序执行阶段：

1. `prepare`（如存在）
2. `build`（如存在）

每个阶段中的 `commands` 按顺序执行，一旦某条命令失败（返回非 0），整个构建终止。

---

## OS 支持说明

目前 `run_build.py` 中的 OS-specific runner 逻辑：

- 通过 `/etc/os-release` 检测 OS 名称与版本；
- 若检测到 `NAME` 包含 `"Anolis"` 且 `VERSION_ID` 以 `"23"` 开头，则使用 `Anolis23Runner`；
- 否则使用 `UnsupportedOsRunner`：

  - 所有 `install_system_packages` / `install_node` / `install_rust` 调用都会报错并退出。

也就是说：

- 当前版本 **只支持在 AnolisOS 23 容器中使用**；
- 若需要支持其他 OS（如 Anolis 8、Alinux、Debian 等），需要扩展 `OsRunnerBase` 的实现，并在 `detect_os_runner` 中加入相应逻辑。
