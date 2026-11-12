# 新流程真机测试指南

> **状态：测试中，邀请真机测试。** 新流程（`tools/tb710fu.py`）的每一步都能在电脑上完整跑通，
> 刷入命令在模拟环境里测过，**但还没有在任何一台真机上写入过**。第一批真机测试就是要回答"能不能开机、哪些功能正常"。
> 想要一台稳定的日常设备，请等测试结果，或者用 [刷机指南](flash-guide.md) 里已经实测过的旧方案。

新流程只用官方来源：联想原厂组件（从你的平板读，或联想官方 9008 包）+ 小米平板 8（yupei）官方 OTA，
在你自己的电脑上构建一个**只属于你这台平板**的刷机包（绑定芯片序列号、当前槽、回滚索引），再用 9008 写入。
仓库不分发任何镜像。原理和每一步的细节见 [boot-chain.md](boot-chain.md) 和 [porting.md](porting.md)。

## 0. 先读：风险和硬性规则

- 9008 写入有变砖风险，数据会被清空，可能失去保修。一切后果自负。
- **唯一数据分区永远不写、不擦**：`persist`、`modemst1/2`、`fsg`、`fsc`、`devinfo`、`frp`、`keystore`、`ssd`、`secdata`、
  `uefivarstore`、`cdt`、`ddr`、`proinfo`、`lenovolock`。工具本身会拒绝写它们，但请不要用别的工具绕过。
- **先完整备份**（第 2 步），并把备份目录另外复制一份到别的盘。里面的唯一数据（IMEI、校准、密钥）丢了找不回来。
- 不要恢复旧的 userdata 原始镜像（TEE 防回滚，PIN 会永远解不开）。
- 开机向导里**不要登录小米账号、不要打开"查找设备"**。
- 出错时先停下，把输出原样发出来，不要反复重试写入。写入读回不一致时工具会停在 9008，**不要拔线重启**。
- 用 AI Agent 帮你操作时，让它读 [`AGENT.md`](../AGENT.md)；写入前的确认词（`flash`、`wipe`、`restore`）应由你自己看过计划后决定。

## 1. 准备

| 项目 | 要求 |
|---|---|
| 平板 | TB710FU。原厂系统任意版本都可以先跑 `check`；ABL 必须信任 AOSP 测试密钥（`check` 会告诉你） |
| 电脑 | Linux（构建已在 Ubuntu 上完整跑通）或 macOS（`check` / `backup` / `pull` 实测过，构建步骤用的是同样的工具）。Windows 暂不支持 |
| 磁盘 | 建议至少 150 GB 空闲：完整备份约 25 GB、联想包 7–8 GB、小米 OTA 约 10 GB、导入后 12 GB、构建中间产物和刷机包约 40 GB、写入时读回 super 约 11 GB |
| Python | 3.8 以上，只用标准库 |
| 工具 | `e2fsprogs`、`erofs-utils`（`mkfs.erofs` 需支持 `--tar`）、`lz4`、`7zz` 或 `7z`（读 .7z 包）、[bkerler/edl](https://github.com/bkerler/edl) |
| AOSP 工具 | `avbtool.py` + `testkey_rsa4096.pem`、`testkey_rsa2048.pem`（AOSP external/avb）放到 `tools/avb/`；`mkbootimg.py`、`unpack_bootimg.py`（AOSP system/tools/mkbootimg，连同 `gki/` 目录）放到 `tools/mkbootimg/` |
| 9008 加载器 | 联想官方 9008 包里的 `xbl_s_devprg_ns.melf` / `prog_firehose_ddr*.melf`，放到 `work/firehose/`（或 `--loader` 指定） |

```bash
# macOS
brew install e2fsprogs erofs-utils lz4 sevenzip python@3.12
# Debian / Ubuntu
sudo apt install e2fsprogs erofs-utils lz4 7zip python3
# edl（两边一样）
python3 -m venv tools/venv && tools/venv/bin/pip install git+https://github.com/bkerler/edl
```

进 9008：已 root 的系统里 `adb reboot edl`；或拔线 → 长按电源 15–20 秒关机 → 按住音量+和音量−插线。
**每次运行写设备或读设备的命令前都要重新进 9008**（工具要靠刚进 9008 时打印的芯片序列号认平板）。

## 2. 步骤

```bash
python3 tools/tb710fu.py check          # 只读：ABL 是否信任测试密钥，生成设备档案 work/device/latest.json
python3 tools/tb710fu.py backup         # 只读：完整备份（二十多 GB，断了重跑会接着读）——备份目录另存一份！
python3 tools/tb710fu.py pull           # 从备份取联想原厂组件（平板已不是原厂系统时：pull --package 联想官方包.7z）
python3 tools/tb710fu.py import work/yupei-ota_full-OS4.0.5.0.XPZCNXM-user-17.0-4fe489f542.zip
python3 tools/tb710fu.py vendor
python3 tools/tb710fu.py framework
python3 tools/tb710fu.py framework --no-finddevice
python3 tools/tb710fu.py bootchain --framework-dir work/build/framework/OS4.0.5.0.XPZCNXM
python3 tools/tb710fu.py bootchain --framework-dir work/build/framework/OS4.0.5.0.XPZCNXM-nofind
python3 tools/tb710fu.py package        # 刷机包在 work/out/<小米版本>-<芯片序列号>/
python3 tools/tb710fu.py flash --dry-run                # 连上平板做完所有检查、打印写入计划，不写
python3 tools/tb710fu.py flash                          # 第一阶段：启动链 + 不带查找设备的 super，再单独确认清数据
#   → 开机向导（不登录小米账号、不开查找设备）；完成后重新进 9008
python3 tools/tb710fu.py flash --stage after-setup      # 第二阶段：换带查找设备的 super，数据保留
```

- 下载地址：联想官方包见 [boot-chain.md](boot-chain.md)（"检查一台设备 / 一个固件包"一节），小米 yupei OTA 见 [flash-guide.md](flash-guide.md)。
- 每一步都有自己的检查，不通过就停下并说明原因；按提示处理，不要改代码绕过。
- 也可以在构建完成前先跑 `check`，把结论发出来：这本身就是有用的测试数据。

出问题想回到原来的系统：

```bash
python3 tools/tb710fu.py restore --dry-run
python3 tools/tb710fu.py restore        # 用你这台平板自己的备份写回启动链和 super（不碰 userdata）
```

## 3. 要测什么、怎么报告

请用 [**真机测试报告**](https://github.com/lich-wang/tb710fu-hyperos/issues/new?template=device-test.yml) 模板开 issue，
不管成功、失败，还是只跑到 `check` 都欢迎。最有用的信息：

1. **你的平板**：原厂系统的地区和版本（设置 → 关于；或 `check` 输出里的 build 字符串）、`check` 的结论和完整输出；
2. **输入**：联想组件来自你的备份还是官方包（哪个版本）、小米 OTA 的版本；
3. **构建**：在什么系统上、哪一步失败（贴报错）；
4. **写入**：`flash --dry-run`、第一阶段、清数据、第二阶段分别是否成功；
5. **开机**：进入开机向导 / 卡在开机动画 / 反复重启 / 变成 900E（USB 显示 05C6:900E）/ 其他；
6. **功能清单**：WiFi、蓝牙、触屏、手写笔、键盘、摄像头（前 / 后）、扬声器、麦克风、耳机、充电、电量显示、
   亮度和自动亮度、旋转和传感器、120 Hz、USB 数据和网络共享、小米账号登录、人脸解锁、OTA 检查是否报错；
7. **日志**（卡开机或功能异常时）：能进系统就 `adb logcat -b all -d > logcat.txt`、`adb shell dmesg > dmesg.txt`；
   900E 时用 edl 连上会导出转储，找 `md_KBOOT_LOG.BIN`。

## 4. 目前已知、还没确认的

- **内核与固件的搭配**：启动链用的是联想组件里的内核（例如 CN 414 的 6.1.128），平板底层固件不动。
  不同版本的底层固件配别的版本的内核能不能开机，正是要测的。请在报告里写清两边的版本。
- **dm-verity**：默认开着。开着进不了系统时，可以用 `bootchain --no-verity` 重建再试（报告里注明）。
- **海外版（ROW）**：ROW 官方包的 rawprogram 是加密的，`pull --package` 用不了；平板还是原厂系统时可以 `pull` 自己的备份。
- [porting.md](porting.md) 里标了"待真机"的项，例如设备兼容性矩阵缺的两个框架服务、联想自己的 lwky 服务。
- 只验证过 yupei `OS4.0.5.0.XPZCNXM`；更新的小米版本 `import` 会提示未验证，需要 `--force`，结果请在报告里注明。
