# 旧方案的 Agent 操作规程（移植包 + v3 启动链）

> 这是**旧方案**（[刷机指南](flash-guide.md)：HyperOS 3 移植包 + v3 启动链 + HyperOS 4 super）的规程，从原来的 `AGENT.md` 移过来。
> 新流程（`tools/tb710fu.py`）的规程见仓库根目录的 [`AGENT.md`](../AGENT.md)。邀请测试的是新流程，新用户请用新流程。
>
> 旧方案没有新流程那样的统一脚本，[刷机指南](flash-guide.md)里的写入步骤直接调用 `edl`（`qfil`、`w`）。所以旧方案的规则是：
> - **只执行刷机指南里写明的命令**，分区名、文件、参数都照指南，不自己编写指南以外的写入命令或脚本，不改仓库代码；
> - **遇到问题不刷机**：任何检查点不过、报错、看不懂的输出，都停下，不再写入，按 `AGENT.md` 第六节整理报告、经用户同意后提 issue，等维护者回复。

你是一个能执行命令的 AI 编程 Agent，用户请你按旧方案把联想小新 Pad Pro GT（TB710FU）从原装 ZUI 刷成小米澎湃 OS 4（HyperOS 4）。
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
| 2 移植包 | 1 完成 + 用户确认 | edl 无报错、以 reset 结束 | 停下，不要重试写入；保存原始输出，提 issue |
| 3 v3 启动链 | 2 完成 | `make_v3_bootchain.py` 输出的 5 个文件 sha256 与指南表格一致；写入后读回一致；进入开机向导 | sha 不一致：输入不是同一个移植包，停下，不写入；进 900E：停下，提 issue |
| 4 Magisk（可选） | HyperOS 3 + USB 调试 | `adb shell su -c id` → uid=0 | 停下，提 issue，等维护者回复，不要自己写回镜像 |
| 5 HyperOS 4 | 3 | super 元数据校验 OK → 向导完成 → 刷 `super_find` → 设置里能登录小米账号 | 卡开机动画、激活锁或其他问题：停下，不再写入；能连 adb 就取 logcat，提 issue，等维护者回复（激活锁通常是在向导里登录了小米账号） |

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

每完成一个阶段，告诉用户：做了什么（写了哪些分区、校验结果）、检查点是否通过、下一步需要用户做什么。
出错时给出原始错误输出的关键几行，说明你的判断（标明是否推测）和你建议的处理，等用户决定。
