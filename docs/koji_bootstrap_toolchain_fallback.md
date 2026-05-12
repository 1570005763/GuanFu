# Koji Bootstrap Toolchain Fallback

本文记录一个暂未默认启用的增强方案：当历史 Koji repo 已被清理时，使用 Koji tag/event 信息补齐 mock bootstrap chroot 需要的工具链 RPM。

## 背景

OpenAnolis Koji 的 buildroot 记录中包含 `tag_name`、`repo_id`、`repo_create_event_id` / `create_event_id`、`arch` 等信息。Koji 没有单独记录“bootstrap chroot 实际安装了哪些 RPM”的完整清单；`installed_pkgs.log` 记录的是 final buildroot 中实际安装的包，也不包含 bootstrap chroot 里的 `python3-dnf`、`python3-dnf-plugins-core` 等工具链包。

当前 GuanFu 的 installed-pkgs fallback 默认禁用 mock bootstrap：

```python
config_opts['use_bootstrap'] = False
config_opts['use_bootstrap_image'] = False
```

这让当前 executor 内的 dnf/rpm 直接创建 final buildroot，避免历史 repo 不存在时 bootstrap 工具链不可解。该路线更实用，但不是对 Koji bootstrap 创建路径的完全复原。

## 可用 Koji 信息

Koji tag/event 可以回答“某个历史事件点上，某个 tag 可见哪些构建和 RPM”。因此可以从 buildroot 元数据反推出 bootstrap 工具链的候选来源：

```text
buildroot.tag_name
+ buildroot.repo_create_event_id / create_event_id
+ mock.cfg 中的 dnf4_install_command / yum_install_command
-> event-time tag 中可见的 bootstrap 工具链候选 RPM
```

OpenAnolis Koji XML-RPC 暴露了相关接口：

```text
listTagged / listTaggedRPMS
getRPM / listRPMs / listBuildRPMs
getRPMDeps / getRPMHeaders
getBuild / getTaskChildren / listTaskOutput / downloadTaskOutput
```

这些接口可以用来定位候选 RPM、解析依赖关系、校验 payloadhash，并优先从 Koji task output 下载 artifact。

## 目标流程

未来若实现严格 bootstrap 工具链补齐，可以采用以下流程：

```text
读取 buildroot tag/event/arch
-> 解析 mock.cfg 的 bootstrap install command
-> 将请求包名映射到 source package/build
-> 在 tag/event 上查询可见 RPM
-> 用 getRPMDeps/getRPMHeaders 解析依赖闭包
-> 对每个候选 RPM 执行 getRPM/getBuild/task output 定位
-> downloadTaskOutput 下载 RPM
-> 校验 payloadhash/size/buildtime
-> 将 bootstrap 工具链 RPM 加入临时 repo
-> 保持 mock bootstrap 开启并执行 rebuild
```

示例：对于 an23 早期 buildroot，mock 可能需要：

```text
python3-dnf
python3-dnf-plugins-core
```

这些包不一定出现在目标包的 `installed_pkgs.log` 中，但可以在对应 `dist-an23.0-build` tag 的历史 event 上查到候选版本。

## 设计约束

- 不能把“tag/event 可见候选”误写成“Koji 记录了 bootstrap installed package list”。Koji 记录的是 tag/event 状态，不是 bootstrap 安装结果。
- 优先使用 Koji artifact，并校验 Koji 元数据；不要默认从当前 release repo 或外部 Fedora repo 混入包。
- 若 canonical package URL 不可访问，应回退到 `build task -> buildArch task -> downloadTaskOutput`。
- bootstrap 工具链加入临时 repo 后，可能影响 final buildroot 的依赖候选；需要通过 include/exclude 或独立 repo 优先级控制，避免 bootstrap-only 包污染 final buildroot 解析结果。
- 若无法可靠复原 bootstrap 工具链，应保留当前默认策略：禁用 mock bootstrap，并在 report 中明确记录 execution delta。

## 报告建议

严格实现后，`report.json` 可在 `build_environment.repo_fallback.bootstrap_toolchain` 中记录：

```json
{
  "status": "ready",
  "source": "koji_tag_event",
  "tag": "dist-an23.0-build",
  "event_id": 2603070,
  "requested_packages": ["python3-dnf", "python3-dnf-plugins-core"],
  "downloaded": 42,
  "historical_exactness": "event_time_candidate_set",
  "repo_visibility": "bootstrap_only"
}
```

当前默认禁用 bootstrap 时，则记录 `status=disabled`、`use_bootstrap=false`、原始 mock bootstrap 设置和禁用原因。
