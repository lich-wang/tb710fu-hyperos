# 新流程真机测试指南

> **状态：参考平板真机跑通，邀请更多人测试。** 参考平板（作者自己的一台）上已真机跑通：从联想官方原厂 ZUXOS 1.5.04.495 两阶段刷入小米 OS4.0.5.0（2026-11-24；BL 锁着、`verifiedbootstate=green`、dm-verity 开）；之后 `flash --stage upgrade` 不清数据升级两次；装了 ArmadaOS Freedom 后用 `flash --stage system` 升到 OS4.0.12.0，Freedom 不受影响；`restore-stock` 多次刷回联想原厂。还没在真机上用过的：`restore`（写回自己的备份）。
> **目前只有这一台真机**：你的平板原厂版本、之前刷过什么都可能不一样，测试就是要回答"在别的机器上能不能开机、哪些功能正常"。

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
- 出错时先停下，把输出原样发出来，不要反复重试写入。写入读回不一致时工具会停在 9008（没有复位）：先保存输出、发出来，不要重试写入或开机；
  要写回备份时，拔线 → 长按电源 15–20 秒关机 → 按键重新进 9008 → `restore --dry-run` → `restore`。
- 用 AI Agent 帮你操作时，让它读 [`AGENT.md`](../AGENT.md)：它只用仓库里的脚本，遇到问题停下不刷机，把报告给你看过后用 `gh` 提 issue；
  写入前的确认词（`flash`、`wipe`、`setup-done`、`restore`）应由你自己看过计划后决定。

## 1. 准备

| 项目 | 要求 |
|---|---|
| 平板 | TB710FU。原厂系统任意版本都可以先跑 `check`；ABL 必须信任 AOSP 测试密钥（`check` 会告诉你） |
| 电脑 | Linux（构建已在 Ubuntu 上完整跑通）或 macOS（`check` / `backup` / `pull` 实测过，构建步骤用的是同样的工具）。Windows：在 WSL2（Ubuntu 24.04）里用，见下面的「Windows（WSL2）」，**还没有人实测过** |
| 磁盘 | 建议至少 150 GB 空闲：完整备份约 25 GB、联想包 7–8 GB、小米 OTA 约 10 GB、导入后 12 GB、构建中间产物和刷机包约 40 GB、写入时读回 super 约 11 GB |
| Python | 3.8 以上，只用标准库 |
| 工具 | `e2fsprogs`、`erofs-utils`（`mkfs.erofs` 需支持 `--tar`）、`lz4`、`7zz` 或 `7z`（读 .7z 包）、[bkerler/edl](https://github.com/bkerler/edl)、[GitHub CLI](https://cli.github.com/)（`gh`，已 `gh auth login`，用来提交报告） |
| AOSP 工具 | 仓库自带，不用另外下载：`tools/avb/`（`avbtool.py` 和两把测试密钥）、`tools/mkbootimg/`（`mkbootimg.py`、`unpack_bootimg.py`）、`tools/apksign/`（重签小米相机的 AOSP 测试密钥），见 [tools/THIRD_PARTY.md](../tools/THIRD_PARTY.md) |
| 小米相机前摄修复 | Java（JDK）、[apktool](https://apktool.org/)、`zipalign`、`apksigner`（Android SDK build-tools）：`framework` 给小米相机打补丁要用（见 [porting.md](porting.md) 的 #48） |
| 9008 加载器 | 仓库自带（`firmware/lenovo-zuxos-495/xbl_s_devprg_ns.melf`，联想官方原厂 495 线刷包里的那份），不用另外准备；`work/firehose/` 里放了别的、或 `--loader` 指定时用那个 |

```bash
# macOS
brew install e2fsprogs erofs-utils lz4 sevenzip python@3.12 gh openjdk apktool
# zipalign / apksigner：Android SDK build-tools（安装时要接受 Android SDK 许可）。
# Homebrew 的 openjdk 是 keg-only，sdkmanager 找不到它，先在当前终端指过去：
export JAVA_HOME="$(brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home" PATH="$(brew --prefix openjdk)/bin:$PATH"
brew install --cask android-commandlinetools && sdkmanager "build-tools;36.0.0"
# Debian / Ubuntu
sudo apt install e2fsprogs erofs-utils lz4 7zip python3 python3-venv gh default-jre-headless apktool zipalign apksigner
# edl（两边一样）
python3 -m venv tools/venv && tools/venv/bin/pip install git+https://github.com/bkerler/edl
```

### Windows（WSL2，还没有人实测过）

Windows 上不能直接运行：构建要 Linux 的 e2fsprogs / erofs-utils，还要保留每个文件的属主、权限和 SELinux 标签。
WSL2 里就是一个真正的 Ubuntu，构建和 Linux 完全一样；9008 设备用微软推荐的 [usbipd-win](https://github.com/dorssel/usbipd-win) 挂进 WSL。
这条路**还没有人在真机上走通过**，第一次用请照常提刷机报告，写明是 WSL2。

1. 装 WSL2 和 Ubuntu（管理员 PowerShell）：`wsl --install -d Ubuntu-24.04`，重启后打开 Ubuntu 设好用户名。
   已经有 WSL1 的发行版：`wsl --set-version <名字> 2`。Ubuntu 24.04 的 erofs-utils 支持 `--tar`，更老的版本不一定。
2. **仓库要克隆到 WSL 自己的文件系统里**（例如 `cd ~ && git clone https://github.com/lich-wang/tb710fu-hyperos && cd tb710fu-hyperos`，之后的命令都在这个目录里运行），
   不要放在 `/mnt/c/...`：Windows 的盘不区分大小写、存不了文件权限和扩展属性，构建会出错，`doctor` 会拒绝。
   WSL 默认最多用一半内存、磁盘放在 C 盘的虚拟磁盘里：C 盘要留出 150 GB 以上。
3. 在 Ubuntu 里按上面「Debian / Ubuntu」一行装工具，再装 edl（同上）。
4. 装 usbipd-win（管理员 PowerShell）：`winget install --interactive --exact dorssel.usbipd-win`（`--interactive`：装驱动要重启时先问你，不会直接重启）。
5. 每次要用 9008 时：平板按住音量+插线进 9008 → 管理员 PowerShell 里 `usbipd list` 找到 `05c6:9008` 那一行的 BUSID →
   `usbipd bind --busid <BUSID>`（同一个 USB 口只要做一次）→ `usbipd attach --wsl --busid <BUSID> --auto-attach`，**这个窗口一直开着**。
   平板每次重新进 9008、工具每次让平板复位，USB 都会断开重连，`--auto-attach` 会自动重新挂进 WSL。
   在 Ubuntu 里 `lsusb` 能看到 `05c6:9008` 就说明挂进来了。
6. 之后的步骤和 Linux 完全一样（udev 规则也一样装）。`doctor` 会认出 WSL2 并检查工作目录的位置。

### 环境检查（动平板之前必须过）

```bash
python3 tools/tb710fu.py doctor                       # 工具、AOSP 工具和测试密钥、9008 加载器、小米底包、磁盘、GitHub CLI、USB 驱动
python3 tools/tb710fu.py doctor --verify-downloads    # 同上，另外校验小米底包的 md5（下载完做一次）
python3 tools/tb710fu.py doctor --usb                 # 9008 实操：按提示用按键手动进 9008，只读测试通信，然后复位
```

- `doctor` 每一项没准备好都会给出处理办法，缺的下载会直接给出地址和字节数。全部 ✅ 再往下走。
- **小米底包**（必需）：yupei 卡刷全量包（约 10 GB），放到 `work/`。验证过的版本任选一个，推荐最新的；地址 `doctor` 会打印：
  - `OS4.0.12.0.XPZCNXM`（2026-11-03，推荐）：
    `https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/OS4.0.12.0.XPZCNXM/yupei-ota_full-OS4.0.12.0.XPZCNXM-user-17.0-e8fb5f8e57.zip`
  - `OS4.0.5.0.XPZCNXM`：
    `https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/OS4.0.5.0.XPZCNXM/yupei-ota_full-OS4.0.5.0.XPZCNXM-user-17.0-4fe489f542.zip`

  文件名最后的 10 位十六进制是 md5 的前 10 位，`--verify-downloads` 会核对。大文件用 `aria2c -c -x 16 -s 16 <地址>`（可断点续传）。
  下面命令里的版本号换成你下载的那个。
- **联想官方 9008 包**（视情况）：平板还是原厂系统时不需要（`pull` 从你自己的备份取；9008 加载器、原厂 495 要换的 CN 279 abl 仓库自带）；
  已刷过别的系统时要用它取联想组件。地址见 [boot-chain.md](boot-chain.md) 或 `doctor` 的输出。
- **USB 驱动**：macOS 不需要装驱动；Linux 要有高通 9008（`05c6`）的 udev 规则，ModemManager 最好先停掉，`doctor` 会检查；Windows（WSL2）用 usbipd-win 把 9008 设备挂进 WSL，见「Windows（WSL2）」。
  用能传数据的线，直接插电脑上的口。
- **手动进 9008 必须会**：刷机中途平板开不了机时，只能靠按键进 9008 救回来，所以 `doctor --usb` 要求用按键进：
  拔线 → 长按电源 15–20 秒彻底关机 → 按住音量+（只按音量+）→ 插线（屏幕保持全黑就对了）。`doctor --usb` 通过了才继续。
  平板已 root 时也可以 `adb reboot edl`，但不能代替这一步练习。

**每次运行读设备或写设备的命令前都要重新进 9008**（工具要靠刚进 9008 时打印的芯片序列号认平板；上一条命令结束时会让平板复位）。

## 2. 步骤

```bash
python3 tools/tb710fu.py doctor --usb   # 环境和 9008 都通了再开始（见上一节）
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

已经刷好之后，小米出了新版本、或者本项目有了新修复（例如 #48 的小米相机前摄），重新 `import` / 构建 / `package`，然后不清数据升级：

```bash
python3 tools/tb710fu.py flash --stage upgrade --dry-run   # 打印计划：super、vbmeta_system，和有变化的启动链镜像
python3 tools/tb710fu.py flash --stage upgrade             # 确认词 upgrade、flash；数据保留
```

升级前请把个人文件另外备份：userdata 不能回滚，升级后开不了机只能清数据重刷；`restore` 写回原系统后同样要清数据。
升级只用于这台电脑两阶段刷好、之后没被 `restore` / `restore-stock` / 别的工具改过的平板（工具会核对）。**不要用系统自带的更新**（#49）。

- 每一步都有自己的检查，不通过就停下并说明原因；按提示处理，不要改代码绕过。
- 也可以在构建完成前先跑 `check`，把结论发出来：这本身就是有用的测试数据。

出问题想回到原来的系统：

```bash
python3 tools/tb710fu.py restore --dry-run
python3 tools/tb710fu.py restore        # 用你这台平板自己的备份写回启动链和 super（不碰 userdata）
```

想从联想原厂系统重新开始，或者 `restore` 因为分区表变了用不了：`restore-stock` 用联想官方原厂 ZUXOS 1.5.04.495 线刷包恢复
（会清空数据，不写唯一数据分区；也要先有这台平板的完整备份；包有解压密码，本仓库不提供）。
包的地址：`https://zsk-cdn.lenovows.com/%E7%9F%A5%E8%AF%86%E5%BA%93/Flash_tool_image/TB710_ZUXOS_1.5.04.495_Tool.7z`（10156610508 字节，sha256 `d225179f…`，工具会核对；`--package` 指向下好的文件，不指定时工具自己下载）。详见 [boot-chain.md](boot-chain.md) 的"恢复联想官方原厂"。已在参考平板上真机恢复成功过。

```bash
python3 tools/tb710fu.py restore-stock --dry-run --package TB710_ZUXOS_1.5.04.495_Tool.7z
python3 tools/tb710fu.py restore-stock --package TB710_ZUXOS_1.5.04.495_Tool.7z
```

## 3. 要测什么、怎么报告

请用 [**刷机报告**](https://github.com/lich-wang/tb710fu-hyperos/issues/new?template=flash-report.yml) 模板开 issue，
不管成功、失败，还是只跑到 `check` 都请提交。先跑一下（只读，不连平板）：

```bash
python3 tools/tb710fu.py report     # 原来的版本线索、ABL 状态和公钥、回滚索引、底层固件版本、联想 / 小米输入、刷机包
```

把输出整段贴进模板的“工具生成的信息”。模板里还要填：

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

- **装了 ArmadaOS Freedom 的平板**：Freedom 安装器改过 boot / vbmeta / vbmeta_system / recovery，`flash` 的第一、第二阶段会拒绝。
  升级安卓只能用 `flash --stage system`（先重新 `backup`），见 [boot-chain.md](boot-chain.md) 的"装了 ArmadaOS Freedom 之后升级澎湃 OS"。
  装完 Freedom 后安卓第一次开机会清数据：先 `--variant nofind` 过开机向导，再 `--variant find`。
  不要在澎湃 OS 里接受系统更新。

- **内核与固件的搭配**：启动链用的是联想组件里的内核（例如 CN 414 的 6.1.128），平板底层固件不动。
  不同版本的底层固件配别的版本的内核能不能开机，正是要测的。请在报告里写清两边的版本。
- **dm-verity**：默认开着。开着进不了系统时，两份启动链都要加 `--no-verity` 重建，再重新打包（报告里注明）：
  ```bash
  python3 tools/tb710fu.py bootchain --no-verity --framework-dir work/build/framework/OS4.0.5.0.XPZCNXM
  python3 tools/tb710fu.py bootchain --no-verity --framework-dir work/build/framework/OS4.0.5.0.XPZCNXM-nofind
  python3 tools/tb710fu.py package
  python3 tools/tb710fu.py flash --dry-run
  ```
- **联想官方原厂 495**：它的 abl 只认联想自己的密钥，刷机时会把当前槽 abl 换成 CN 17.0.04.279 的（见 [boot-chain.md](boot-chain.md) 的"联想官方原厂 495 的 abl"）。
  这个判断来自静态分析和 Y700 四代的实证，TB710FU 原厂 495 上的第一次刷入正是要验证它。报告里请写明 `check` 的结论。
- **海外版（ROW）**：ROW 官方包的 rawprogram 是加密的，`pull --package` 用不了；平板还是原厂系统时可以 `pull` 自己的备份。
- [porting.md](porting.md) 里标了"待真机"的项，例如设备兼容性矩阵缺的两个框架服务、联想自己的 lwky 服务。
- 验证过的小米版本：yupei `OS4.0.5.0.XPZCNXM`、`OS4.0.12.0.XPZCNXM`。更新的版本 `import` 会提示未验证，需要 `--force`，结果请在报告里注明；
  验证通过后由维护者加进 `tools/tb/xiaomi.py` 的 `VERIFIED` 和 `tools/tb/doctor.py` 的 `XIAOMI`。
