# 本地重复构建指南

本指南将介绍如何使用 GuanFu 在本地环境中基于指定的 `buildspec.yaml` 文件实现重复构建。

## 前提条件

在开始本地重复构建之前，请确保您已满足以下条件：

1. 已安装 Docker
2. 已安装 Python 3.6+
3. 已安装 Git
4. 已安装 YAML 解析器（PyYAML 包）

## 使用方法

GuanFu 提供了 `build-runner.sh` 脚本，默认以容器模式运行，即启动容器并在容器中运行构建。

为了兼容既有用法，脚本入口会继续保留：

```bash
# 在 GuanFu 项目根目录下运行
./src/build-runner.sh path/to/your/buildspec.yaml
```

如果未指定 `buildspec.yaml` 文件路径，则默认使用 `buildspec.yaml`。

同时，GuanFu 也提供统一的 Python 命令行入口：

```bash
python3 -m pip install -e .
guanfu rebuild buildspec --spec path/to/your/buildspec.yaml
```

`guanfu rebuild buildspec` 当前会薄包装既有 `build-runner.sh` 逻辑，因此两种方式的构建行为保持一致。

脚本会：
- 从 buildspec.yaml 文件中读取容器镜像配置
- 解析 buildspec.yaml 中的 outputs 部分，将输出文件目录挂载到容器中
- 将 buildspec.yaml 文件和 scripts 目录挂载到容器中
- 启动相应的容器并在其中执行构建
- 构建完成后，输出文件会自动保存到宿主机对应目录中

## 构建过程

当运行 `build-runner.sh` 脚本时，将在容器内执行以下步骤：

1. 确保容器内有 python3 和 pip
2. 安装 PyYAML（如果尚未安装）
3. 运行 `run_build.py` 脚本来执行实际的构建过程

`run_build.py` 脚本会按以下顺序执行：

1. 处理 inputs（下载/解压/克隆/检查）
2. 配置默认环境参数
3. 根据 environment 部分安装系统包和工具
4. 执行 phases 中定义的构建阶段命令

## OpenAnolis Koji RPM rebuild

GuanFu 还提供本地 Koji RPM rebuild 子命令，用于验证已经发布出来的 OpenAnolis RPM：

```bash
guanfu rebuild koji-rpm \
  --rpm-name zlib-1.2.13-3.an23.x86_64.rpm
```

`koji-rpm` 默认走容器执行器。当前容器执行器只支持 an23 RPM；如果 Koji buildroot tag 或 RPM release 不能识别为 an23，GuanFu 会返回 `unsupported`，后续 an8、alinux3、alinux4 等发行版会由未来的 policy 模块接入。

默认流程会：

1. 根据 RPM 名称查询 OpenAnolis Koji，定位 buildroot 和构建任务
2. 判断目标为 an23 后，选择 an23 GuanFu rebuild 容器镜像
3. 挂载 `--workdir` 到容器内 `/work`，并在容器内以 `--executor local` 重新执行
4. 从发布 source repo 获取 SRPM，并下载 Koji task SRPM/logs 做同源校验
5. 使用 Koji `buildroot_id` 生成 `mock.cfg`
6. 使用容器内的 `mock --rebuild` 重建发布 SRPM
7. 对比发布 RPM 和本地 rebuild RPM，并输出 `report.json`

如果需要保留当前 host 上直接运行 mock 的行为，可以显式使用 local executor：

```bash
guanfu rebuild koji-rpm \
  --rpm-name zlib-1.2.13-3.an23.x86_64.rpm \
  --executor local
```

容器执行器的可选参数：

```text
--container-runtime auto|podman|docker
--container-image IMAGE
--container-privileged / --no-container-privileged
```

`auto` 在 Linux 上优先选择 `podman`，其它平台优先选择 `docker`。默认会传 `--privileged`，因为容器内的 `mock` 需要创建 buildroot/chroot 并执行 RPM 安装事务。

容器执行器会在运行 mock 前改写生成的 `mock.cfg`，将 chroot 内的 `mockbuild` 用户固定为非 root UID，并保留 mock/Koji 配置给出的 group。这样外层容器仍可用 root/privileged 完成 mount 和 chroot 操作，但 SRPM 的 `%build`/`%check` 不会以 root 语义运行。

### VM 环境建议

需要注意，容器执行器解决的是工具链和依赖隔离问题，但容器内的 `mock` 仍然共享宿主机的 kernel 和 CPU 特征暴露。如果历史 buildroot 较旧，在当前宿主或容器环境中执行 RPM scriptlet、`bash`、`glibc` 或 buildroot 工具时，可能出现类似下面的失败：

```text
scriptlet failed, signal 11
Segmentation fault
Illegal instruction
invalid opcode
Transaction failed（通常伴随 scriptlet signal 4/11）
```

这类失败通常不表示 SRPM、`mock.cfg` 或依赖恢复一定有问题，而可能是本地执行环境和 Koji builder 的 CPU/kernel 边界不一致。此时可以在 Linux VM 中运行同一条 GuanFu 命令，通过 KVM/QEMU 固定更接近 Koji builder 的 CPU model。当前 an23 实验中，`Haswell-v4` 和 `Cascadelake-Server-v5` VM 均已验证可以让 old an23 buildroot 正常运行并完成 `attr-2.5.1-5.an23` rebuild。

GuanFu 会在 mock 失败后读取 `root.log`、`build.log` 和 `state.log`。如果检测到上述旧 buildroot 运行时崩溃特征，`report.json` 的 `rebuild.failure_diagnosis` 会给出 `buildroot_runtime_incompatible` 诊断、证据行和 VM 重试建议。

如果原始 Koji buildroot repo 已被清理，默认会启用 `installed-pkgs` fallback：

```text
installed_pkgs.log
-> Koji RPM: getRPM -> getBuild/getTaskChildren/listTaskOutput -> downloadTaskOutput
-> external RPM: getExternalRepoList(tag,event) -> repodata primary -> RPM download -> header 校验
-> mock bootstrap: 当前 mock 有效配置 -> bootstrap 工具链 RPM 下载
-> createrepo_c -> 临时本地 repo -> mock
```

该 fallback 会根据目标构建任务的 `installed_pkgs.log` 恢复当时实际安装进 buildroot 的依赖 RPM。Koji 自身产物会校验 `payloadhash` 并从 task output 下载；Koji 索引中不存在的 external RPM 会按 buildroot 的 tag/event 查询当时绑定的 external repo，再通过 repodata 找到 RPM，并校验 NEVRA、SIGMD5、安装后 size 和 buildtime。为了让本地 mock 能创建 bootstrap chroot，GuanFu 还会按当前本地 mock 有效配置解析 `python3-dnf` 等 bootstrap 工具链包，并把解析到的 RPM 加入临时 repo。随后 GuanFu 会把 `mock.cfg` 的 repo `baseurl` 改写为这个临时本地 repo。

需要注意：external repo 的定义可由 Koji event 查询，但 external repo 内容本身不是 OpenAnolis Koji artifact；bootstrap 工具链来自当前本地 mock 配置，不等同于历史 Koji builder 的确定输入。因此报告会在 `build_environment.repo_fallback.external_repo_recovery` 和 `build_environment.repo_fallback.bootstrap_toolchain` 中记录来源、校验结果和 `historical_exactness`。若依赖解析、校验、task output 定位或下载不完整，GuanFu 不会静默降级到当前 release repo。

可以关闭该行为，恢复为历史 repo 不可用即跳过：

```bash
guanfu rebuild koji-rpm \
  --rpm-name grep-3.11-1.an23.x86_64.rpm \
  --repo-fallback none
```

`report.json` 会包含轻量差异摘要：

- RPM header 字段差异，例如 `BUILDTIME`、`BUILDHOST`、`PAYLOADDIGEST`、`RSAHEADER`
- 文件清单差异，例如只存在于发布 RPM 或 rebuild RPM 的文件
- 文件属性差异，例如 `mtime`、`digest`、`size`、`mode`、owner/group
- 差异分类，例如 `RPM_SIGNATURE`、`RPM_METADATA`、`FILE_TIMESTAMP`、`FILE_PERMISSION`、`FILE_ADDED`、`FILE_REMOVED`、`SYMLINK_TARGET`、`DOC_CONTENT`、`CONFIG_CONTENT`、`SCRIPT_CONTENT`、`COMPRESSION`、`DEBUG_INFO`、`OTHER`
- 汇总判断，例如 `risk_level`、`action`、`reproducible`、`confidence`、`trust_level`、`diff_by_risk_level`

当前默认使用 `light` 分析模式。该模式只依赖 RPM header、RPM 文件清单、scriptlet 和路径规则，不会解包并解析 ELF section 或 BuildID。因此，当可执行文件或动态库内容发生变化时，报告会保守输出 `OTHER`，并通过 `possible_diff_types` 标记可能属于 `BINARY_CODE`、`BINARY_BUILDID` 或 `DEBUG_INFO`。默认报告不包含原始 header/file diff，原始 Koji 元数据会保存在本次运行目录的 `metadata/` 下。

`trust_level` 映射为：

- `L4`: 发布 RPM 与本地 rebuild RPM 字节级一致
- `L3`: 只有时间戳、签名、BuildID 等预期构建环境差异
- `L2`: 差异均可解释，且没有安全相关异常
- `L1`: 存在少量非预期差异，但没有安全风险迹象
- `L0`: 存在不可解释差异或安全相关异常

默认 Koji 配置：

```text
koji server: https://build.openanolis.cn/kojihub
koji topurl: https://build.openanolis.cn/kojifiles
source RPM base URL: https://mirrors.openanolis.cn/anolis/23/os/source/Packages/
binary RPM base URL: https://mirrors.openanolis.cn/anolis/23/os/x86_64/os/Packages/
```

默认容器执行器需要本机安装 `podman` 或 `docker`。容器镜像内需要包含 `guanfu`、`koji`、`mock`、`rpm`、`dnf` 和 `createrepo_c`。local executor 需要 Linux 环境，并在 host 上安装这些工具；macOS 不能原生运行 local mock，需要使用容器、Linux 虚拟机或远程 Linux 主机。
