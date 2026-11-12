# AGENT.md — 给执行刷机的 AI Agent 的操作规程

你是一个能执行命令的 AI 编程 Agent，用户请你按本仓库把联想小新 Pad Pro GT（TB710FU）从原装 ZUI 刷成小米澎湃 OS 4（HyperOS 4）。
本文件是你的**规程**，`docs/flash-guide.md` 是**步骤**，`docs/*.md` 是背景与排错。开始前把这三类文档都读完。

用户通常不是 Android/Linux 底层专家。用中文（或用户使用的语言）和用户交流，说清楚每一步要做什么、为什么、风险在哪。

---

## 一、硬性规则（任何情况下都不能违反）

1. **唯一数据分区永不写入、永不擦除**：`persist`、`modemst1`、`modemst2`、`fsg`、`fsc`、`devinfo`、`frp`、`keystore`、`ssd`、
   `secdata`、`uefivarstore`、`cdt`、`ddr`。不在任何 `edl w/wl/e/es/ws/qfil` 的目标里出现它们。执行 `edl qfil` 前，
   先检查 rawprogram XML：这些 label 的 `filename` 必须为空。
2. **先备份再写**：第 1 步（GPT + 除 userdata/super 外的所有分区）没有完成并核对文件存在、大小非零之前，不做任何写入。
3. **每次写设备之前都要用户明确确认**。先列出：要写的分区、源文件、源文件 sha256、完整命令、可能的后果和回退办法，
   然后停下来等用户回复"确认/yes"。上一次的确认不覆盖下一次写入。读操作（备份、printgpt、getprop）不需要确认。
4. **写后校验**：能读回的都读回比对 sha256（`edl r`/`rs`、`dd | sha256sum`）。不一致就停下，报告给用户，不要继续。
5. **不改 GPT**，除非是本指南明确要求的步骤（第 2 步整包刷写自带 GPT）。
6. **不刷 ABL/XBL 等底层引导到非移植包提供的版本**，不刷 ROCKNIX ABL（这台机器 BL 锁定，换 ABL 有变砖风险）。
7. **不要恢复旧的 userdata 原始镜像**（TEE 防回滚，会导致 PIN 永远解不开）。
8. **不要打开小米"查找设备"**，开机向导里不要登录小米账号（第 5 步）。
9. 进程管理只按 PID 结束进程，不用 `pkill -f`、`killall` 这类宽泛匹配。
10. 推测要明说是推测。不确定时问用户或停下，不要编造步骤、分区名、偏移或校验值。
11. 不在命令行、日志、文件里回显用户的密码、WiFi 密码、密钥。

## 二、需要用户动手的事

你无法替用户做这些，到这一步时说清楚怎么做，然后等用户回复"好了"：

- **进入 9008**：关机拔线 → 长按电源 15–20 秒 → 按住音量+和音量−再插线（或在已 root 的 Android 下 `adb reboot edl`）
- 平板上点击：开机向导、USB 调试授权、Magisk 授权（"允许 Shell 获取 root"）
- 下载需要账号或人工确认的文件（移植包、OTA），放到 `work/` 的约定位置
- 拔插数据线、短按 / 长按电源键

## 三、判断设备当前状态

| 检测 | 含义 |
|---|---|
| `system_profiler SPUSBDataType` 有 `05c6:9008`（QUSB__BULK） | 9008/EDL，可以用 edl |
| 有 `05c6:900e` | 内核崩溃转储模式：用 edl 导出转储分析，不要直接重写 |
| `adb get-state` = `device` | Android（HyperOS）运行中；`adb shell su -c id` 判断是否 root |
| `fastboot devices` 有设备 | fastboot（misc 里的 bootonce-bootloader）：`fastboot reboot` |
| 都没有 | 平板关机、睡眠或线没插好：请用户检查 |

## 四、阶段与检查点

严格按 `docs/flash-guide.md` 的顺序。每一阶段开始前确认上一阶段的检查点已通过；做完向用户报告结果和下一步。

| 阶段 | 进入条件 | 完成标志（检查点） | 失败时 |
|---|---|---|---|
| 0 环境 | — | `tools/venv/bin/edl --help` 可用；`tools/avb/` 有 avbtool 和 rsa2048/4096 测试密钥；ZUI 版本 = 1.5.04.495 | 版本不对就停止 |
| 1 备份 | 9008 | `work/backup/gpt` 6 个 LUN；`work/backup/parts` 含全部唯一分区且大小非零 | 不要进入下一步 |
| 2 移植包 | 1 完成 + 用户确认 | edl 无报错、以 reset 结束 | 报告错误输出；不要重试写入，先让用户决定 |
| 3 v3 启动链 | 2 完成 | `make_v3_bootchain.py` 输出的 5 个文件 sha256 与指南表格一致；写入后读回一致；进入开机向导 | sha 不一致：输入不是同一个移植包，停下问用户；进 900E：导出转储分析 |
| 4 Magisk（可选） | HyperOS 3 + USB 调试 | `adb shell su -c id` → uid=0 | 写回 `work/v3/{init_boot,vbmeta}.img` |
| 5 HyperOS 4 | 3 | super 元数据校验 OK → 向导完成 → 刷 `super_find` → 设置里能登录小米账号 | 卡开机动画：调试版 super + logcat（`docs/hyperos4.md`）；激活锁：说明是在向导里登录了账号；回退：写回移植包的 super |

## 五、已知故障特征

| 输出 / 现象 | 原因 | 处理 |
|---|---|---|
| `erofs: blkszbits 14 isn't supported` | mkfs.erofs 默认 16K 块 | 构建脚本已带 `-b4096`；检查 erofs-utils 版本 |
| init 停在 "Compiling SELinux policy" | 平台策略删了 vendor 仍引用的类型 | `hyperos/sepolicy_compat.cil` 已处理；新底包需重新跑 `tools/sepolicy_fix.py` |
| zygote 每 5 秒 SIGABRT（MediaProfiles） | `media.settings.xml` 指向不存在的文件 | `build_os4.py` 已注释该属性 |
| `cloudsp_fid 参数不能为空` | 小米查找设备需要小米 TEE | 用 `super_nofind` 过向导，再刷 `super_find` |
| `IKMHal_sendCmd status -30`、PIN 解不开 | 写回了旧 userdata | 只能清数据重走向导 |
| `adb reboot bootloader` 后 USB 变成 900E | 本机出现过，原因未查明 | 不要依赖 fastboot，用 9008 |

## 六、汇报格式

每完成一个阶段，告诉用户：做了什么（写了哪些分区、校验结果）、检查点是否通过、下一步需要他做什么。
出错时给出原始错误输出的关键几行，说明你的判断（标明是否推测）和你建议的处理，等用户决定。

---

## 六点五、新流程（tools/tb710fu.py，测试中）

用户要用新流程时，步骤以 [`docs/testing.md`](docs/testing.md) 为准，第一节的硬性规则照样适用，另外：
- `flash` / `restore` 会打印写入计划并要求输入确认词（`flash`、`wipe`、`restore`）。先把计划原样给用户看，
  用户在对话里明确同意后才输入对应的确认词；`wipe` 会清空平板数据，必须单独问。不要用管道或环境变量自动确认。
- 先跑 `flash --dry-run`。任何检查失败、读回不一致都停下，把原始输出给用户，不要改代码、加参数或换工具绕过。
- 读回不一致时平板留在 9008：不要让用户拔线或重启，先报告；需要回退时用 `restore`。
- 每次读写设备前都要重新进 9008（工具靠芯片序列号认平板）。
- 结果按 `.github/ISSUE_TEMPLATE/device-test.yml` 整理，帮用户提交真机测试报告；不要贴备份、刷机包或唯一数据分区的内容。

## 七、给本仓库提改动的 Agent：研发规范

以上各节是刷机规程。如果你的任务是**修改本仓库**（代码、文档、CI），另外遵守下面这些。

### 分支与 PR
- `main` 受保护：不直接推送，不强推，不删除。所有改动走「Issue → 特性分支 → PR → 合并」。
- 先开 Issue 说明要做什么，再从最新的 `main` 拉分支，命名 `<类型>/<简短描述>`（如 `fix/super-verity`、`docs/flash-guide`、`chore/ci-cache`）。
- PR 描述必须写 `Closes #<编号>`（或 `Fixes` / `Resolves`），否则 `require-linked-issue` 检查失败，PR 无法合并。
- 一个 PR 只做一件事；合并用 `gh pr merge --squash --delete-branch`。
- 克隆后执行一次 `git config core.hooksPath .githooks`，启用本地防误推钩子（拦截推送到 `main`/`master`）。

### CodeRabbit 质量门禁
- 分支保护要求：必需检查 `require-linked-issue` 和 `CodeRabbit` 通过、分支与 `main` 同步、所有讨论已解决；管理员同样受约束。
- `.coderabbit.yaml` 开了阻塞模式（`request_changes_workflow: true`）：CodeRabbit 发现问题时给出 **Request changes**，
  问题全部修复、讨论全部 Resolve 之后才给 **Approve**。
- 处理流程：逐条看 CodeRabbit 的评论 → 修复并推送（或在讨论里说明为什么不改）→ 把讨论标记为 Resolved →
  等它对最新提交重新审查。
- **`APPROVED` 与 `Success` 不是一回事**：`CodeRabbit` 检查显示 Success 只表示这次审查跑完了，不代表没有问题。
  合并前确认 CodeRabbit 的最新 Review 是 `APPROVED`，**且针对的是 PR 当前的 head 提交**（之后又推送过的话，旧的批准不算数）：
  ```bash
  gh pr view <PR> --json headRefOid --jq .headRefOid
  # --paginate 读完所有页（默认每页 30 条），最后一行就是 CodeRabbit 最新的 Review。
  # pipefail：任何一页请求失败整条命令就失败，此时不要合并，重新查询。
  set -o pipefail
  gh api --paginate repos/{owner}/{repo}/pulls/<PR>/reviews \
    --jq '.[] | select(.user.login == "coderabbitai[bot]") | "\(.state) \(.commit_id)"' | tail -n 1
  ```
  两者的提交号必须一致、`state` 为 `APPROVED`，并确认没有未解决的讨论。
- 在评论里发 `@coderabbitai review` 若回复 “Already reviewed the last commit”，说明最新提交已经审过，直接按上一条查看 Review 状态。

### 红线
- 不绕过门禁：不用管理员权限强制合并，不临时关掉分支保护或必需检查，不改 `.coderabbit.yaml` 来让审查“通过”。
- CodeRabbit 没有 `APPROVED`、或还有未解决的讨论时，**不得向用户汇报“已完成”**；如实说明卡在哪里。
