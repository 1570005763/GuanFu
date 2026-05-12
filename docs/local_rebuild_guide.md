# 本地重复构建指南

本指南介绍如何使用 GuanFu 在本地运行两类 rebuild：基于 `buildspec.yaml` 的既有容器构建流程，以及基于 OpenAnolis Koji 发布 RPM 的本地 VM rebuild 流程。

## 前提条件

运行 buildspec 容器 rebuild 前，请确保您已满足以下条件：

1. 已安装 Docker
2. 已安装 Python 3.6+
3. 已安装 Git
4. 已安装 YAML 解析器（PyYAML 包）

运行 Koji RPM VM rebuild 还需要 Linux host，以及 `koji`、QEMU/libguestfs 工具链；当历史 repo 不存在并启用 installed-pkgs fallback 时，还需要 `createrepo_c`。macOS 不能原生运行 mock，建议使用 Linux VM 或远程 Linux 主机。

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

`koji-rpm` 默认走 VM 执行器。当前 VM 执行器只支持 an23 RPM；如果 Koji buildroot tag 或 RPM release 不能识别为 an23，GuanFu 会返回 `unsupported`，后续 an8、alinux3、alinux4 等发行版会由未来的 policy 模块接入。

默认流程会：

1. 根据 RPM 名称查询 OpenAnolis Koji，定位 buildroot 和构建任务
2. 判断目标为 an23 后，选择 an23 VM profile
3. 从 Koji task output 的 `hw_info.log`、`root.log`、`mock_output.log` 中提取原始 builder 的 CPU/kernel/mock 记录
4. 从发布 source repo 获取 SRPM，并下载 Koji task SRPM/logs 做同源校验
5. 使用 Koji `buildroot_id` 生成 `mock.cfg`
6. 若历史 repo 不存在，使用 `installed_pkgs.log` 恢复临时本地 repo
7. 通过 QEMU 启动 VM；KVM 可用时使用 KVM，否则降级到 TCG，并通过 9p 挂载或 `virt-copy-in` / `virt-copy-out` 交换本次运行目录
8. 在 VM 中执行 `mock --rebuild`
9. 对比发布 RPM 和本地 rebuild RPM，并输出 `report.json`

如果需要保留当前 host 上直接运行 mock 的行为，可以显式使用 local executor：

```bash
guanfu rebuild koji-rpm \
  --rpm-name zlib-1.2.13-3.an23.x86_64.rpm \
  --executor local
```

VM 执行器的可选参数：

```text
--vm-image PATH_OR_URL
--vm-image-format auto|qcow2|raw
--vm-kernel PATH
--vm-initrd PATH
--vm-cpu Cascadelake-Server-v1
--vm-memory 4096M
--vm-smp 2
--vm-share-mode auto|9p|image-copy
--vm-prepare-packages mock,rpm-build
--vm-timeout 7200
--vm-require-kvm
```

`--vm-image` 可以是本地路径，也可以是 URL；未指定时，an23 默认使用 OpenAnolis GA 源中的
`https://mirrors.openanolis.cn/anolis/23/isos/GA/x86_64/AnolisOS-23.4-x86_64.qcow2`。
GuanFu 会把该基础镜像缓存到工作目录下的 `vm-cache/`，每次 rebuild 创建临时 qcow2 overlay，
并通过 `virt-customize` 注入一次性 systemd rebuild 服务。`--vm-kernel`、`--vm-initrd` 仅用于
自定义 raw image 的 direct-init 兼容路径。如果 `/dev/kvm` 可用，GuanFu 会使用 KVM；如果不可用，
默认自动降级到 QEMU TCG，并在 stderr 和 `report.json` 中标记 degraded。严格场景可以使用
`--vm-require-kvm`，使 KVM 不可用时直接失败。

`--vm-share-mode auto` 会优先使用 QEMU 9p 共享目录；如果当前 QEMU 不支持 `virtio-9p-pci`，
GuanFu 会改用 `image-copy`：先用 `virt-copy-in` 把本次输入复制进 qcow2 overlay，VM 关机后再用
`virt-copy-out` 把 `results/` 和 `metadata/` 复制回本地工作目录。

默认 qcow2 overlay 启动前会通过 `virt-customize --run-command` 预装 `mock,rpm-build`，
避免在无 KVM 的 TCG VM 内慢速安装。可通过 `--vm-prepare-packages ""` 关闭。

`--runs` 默认是 `1`，`--vm-timeout` 默认是 `7200` 秒，`--workdir` 默认是 `guanfu-koji-rebuild`。
因此在 host 依赖齐备时，最小命令就是：

```bash
guanfu rebuild koji-rpm \
  --rpm-name zlib-1.2.13-3.an23.x86_64.rpm
```

### VM 环境建议

需要注意，local executor 仍然共享宿主机的 kernel 和 CPU 特征暴露。如果历史 buildroot 较旧，在当前宿主环境中执行 RPM scriptlet、`bash`、`glibc` 或 buildroot 工具时，可能出现类似下面的失败：

```text
scriptlet failed, signal 11
Segmentation fault
Illegal instruction
invalid opcode
Transaction failed（通常伴随 scriptlet signal 4/11）
```

这类失败通常不表示 SRPM、`mock.cfg` 或依赖恢复一定有问题，而可能是本地执行环境和 Koji builder 的 CPU/kernel 边界不一致。默认 VM executor 会通过 KVM/QEMU 固定更接近 Koji builder 的 CPU model。当前 an23 实验中，`Cascadelake-Server-v1` VM 已验证可以让 `acl`、`bzip2` 这类历史 repo 已失效的包通过 installed-pkgs fallback 正常完成 rebuild；`sqlite-libs` 在无 KVM 的 TCG 测试机上进入 `%check` 后触发 7200 秒 timeout，因此这类重包需要在有 KVM 的机器上复测。

GuanFu 会在 mock 失败后读取 `root.log`、`build.log` 和 `state.log`。如果检测到上述旧 buildroot 运行时崩溃特征，`report.json` 的 `rebuild.failure_diagnosis` 会给出 `buildroot_runtime_incompatible` 诊断、证据行和 VM 重试建议。

如果原始 Koji buildroot repo 已被清理，默认会启用 `installed-pkgs` fallback：

```text
installed_pkgs.log
-> Koji RPM: getRPM -> getBuild/getTaskChildren/listTaskOutput -> downloadTaskOutput
-> external RPM: getExternalRepoList(tag,event) -> repodata primary -> RPM download -> header 校验
-> createrepo_c -> 临时本地 repo
-> mock.cfg 改写为本地 repo，并禁用 mock bootstrap
-> mock
```

该 fallback 会根据目标构建任务的 `installed_pkgs.log` 恢复当时实际安装进 buildroot 的依赖 RPM。Koji 自身产物会校验 `payloadhash` 并从 task output 下载；Koji 索引中不存在的 external RPM 会按 buildroot 的 tag/event 查询当时绑定的 external repo，再通过 repodata 找到 RPM，并校验 NEVRA、SIGMD5、安装后 size 和 buildtime。随后 GuanFu 会把 `mock.cfg` 的 repo `baseurl` 改写为这个临时本地 repo。

默认的 installed-pkgs fallback 会禁用 mock bootstrap，即追加：

```python
config_opts['use_bootstrap'] = False
config_opts['use_bootstrap_image'] = False
```

这样 mock 会由当前 executor 内的 dnf/rpm 直接创建 final buildroot，再在 final buildroot 中执行 `rpmbuild`。bootstrap chroot 是创建 buildroot 的工具层，不是 `rpmbuild` 的目标环境；禁用它可以避免历史 repo 清理后 `python3-dnf` / `python3-dnf-plugins-core` 这类 bootstrap 工具链不可解的问题。报告会在 `build_environment.repo_fallback.bootstrap_toolchain` 中记录 `status=disabled`、原始 mock bootstrap 设置和禁用原因。

需要注意：external repo 的定义可由 Koji event 查询，但 external repo 内容本身不是 OpenAnolis Koji artifact；禁用 mock bootstrap 也意味着 buildroot 创建路径不同于原始 Koji builder。因此报告会在 `build_environment.repo_fallback.external_repo_recovery` 和 `build_environment.repo_fallback.bootstrap_toolchain` 中记录来源、校验结果和执行差异。若依赖解析、校验、task output 定位或下载不完整，GuanFu 不会静默降级到当前 release repo。未来更严格的 bootstrap 工具链补齐方案见 [Koji Bootstrap Toolchain Fallback](koji_bootstrap_toolchain_fallback.md)。

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

默认 VM 执行器会在下载 VM 镜像前检查 host 依赖，并给出明确安装提示；不会自动安装 host 系统包。
host 侧需要 QEMU、`qemu-img`、`virt-customize`，如果 QEMU 不支持 9p 或显式使用 `image-copy`，
还需要 `virt-copy-in` 和 `virt-copy-out`。an23 默认 qcow2 镜像来自 OpenAnolis GA mirror，会自动下载
并缓存；VM overlay 内的 `mock,rpm-build` 会自动准备。host 侧还需要 `guanfu`、`koji` 和
`createrepo_c` 用于 installed-pkgs fallback。local executor 需要 Linux 环境，并在 host 上
安装这些工具；macOS 不能原生运行 local mock，需要使用 Linux 虚拟机或远程 Linux 主机。
