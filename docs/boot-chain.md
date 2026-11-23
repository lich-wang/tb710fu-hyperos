# 启动链：锁 BL 下为什么能刷、怎么刷、怎么救

## 设备

- 联想小新平板 Pro GT，型号 **TB710FU**，骁龙 8 Gen 3（SM8650 / pineapple），UFS（LUN 0–5，4 KiB 扇区），256 GB。
- 原厂系统 ZUI 17.5（CN）。**ZUI 1.5.04.496 起无法解锁 Bootloader**；本项目全程在锁定状态下完成。
- 按键都在 PMIC 上：电源键（pwrkey）、音量下（resin）、音量上（PM8550 GPIO6）。

## 为什么锁 BL 也能启动改过的镜像

1. 这台机器的 ABL 验证 vbmeta 时信任 **AOSP 公开测试密钥**：`vbmeta`/`boot` 用 `testkey_rsa4096`、
   `vbmeta_system` 用 `testkey_rsa2048` 签名的镜像都能通过（`verifiedbootstate=green`、`locked`）。
   社区的"免解锁（WITHOUT_UBL）补充包"就是利用这一点。
2. 因此可以修改 `boot`、`vendor_boot`、`init_boot`、`dtbo` 等，只要：
   - 给改过的分区重新加 AVB hash footer（同一测试密钥），并让 `vbmeta` 里的摘要与之匹配；
   - **防回滚索引不能低于设备 RPMB 里记录的值**。实测 ZUI 495 的 `vbmeta_system` 回滚索引是 `1783209600`，
     `boot`（location 3）是 `1736035200`，其余为 0。降级到更低索引的镜像会直接拒绝启动。
3. 只改 boot 时，可以沿用原 boot 的 salt、回滚索引和属性，只替换内容并重签 footer，这样**不需要改 vbmeta**。

工具：AOSP `avbtool`（`info_image` / `add_hash_footer` / `verify_image --follow_chain_partitions`）、
AOSP `mkbootimg` / `unpack_bootimg`。仓库自带原样副本（`tools/avb/`、`tools/mkbootimg/`，来源提交和许可证见 [tools/THIRD_PARTY.md](../tools/THIRD_PARTY.md)）。

### 检查一台设备 / 一个固件包是否信任测试密钥

`tools/tb710fu.py check`（只读，Python 3.8+ 标准库；9008 方式需要 bkerler/edl 和 firehose 加载器）：

```bash
python3 tools/tb710fu.py check                       # 9008（默认）：不需要 root
python3 tools/tb710fu.py check --adb                 # adb + root（Magisk 允许 Shell）
python3 tools/tb710fu.py check 固件文件夹/.zip/.7z/镜像  # 离线：官方线刷包，或 9008 备份出来的 abl_a.img / vbmeta_a.img
```

- **怎么判断**：从当前槽 abl 的 LZMA 固件卷里，找出 LinuxLoader 内置的 AVB 根公钥（锁着 BL 时，vbmeta 必须用这把钥匙签），再和 AOSP 测试公钥比对。找到的公钥都会用 n0inv 和 R² 验算，确认真是一把 RSA 公钥。
- **一并读出**：
  - vbmeta 及其链式分区（boot、recovery、vbmeta_system）的签名公钥和回滚索引；
  - A/B 槽状态；
  - super 的布局和文件系统（ext4 像原厂，EROFS 多半已经刷过别的系统，这一项是推测）；
  - 底层固件分区的 sha256 指纹。
- **输出**：连着平板检查（9008 或 `--adb`）时写入设备档案 `work/device/latest.json`，后面的构建和刷入以它为准。离线检查文件只打印结果，不写档案。
- **9008 方式**：还会核对芯片 HWID 和 OEM 公钥哈希，检查完自动复位开机（`--no-reset` 留在 9008）。

联想公告 LEN-221035（CVE-2026-92777）把海外版 TB710FU 的修复版本定为 **ZUI 17.5.04.059**，但版本号不能代替实际读 abl：
实测公告所说的修复版 ROW 059 的 abl 里，内置的仍然只有 `testkey_rsa4096`。实际读过的联想官方固件：

| 固件 | abl sha256 | abl 内置根公钥 | 官方包 vbmeta 签名 | 官方 vbmeta_system 回滚索引 |
|---|---|---|---|---|
| CN ZUI 17.0.04.279（ST_250808） | `45106e3b…` | testkey_rsa4096 | testkey_rsa4096 | 1749081600 |
| CN ZUI 17.0.04.354（ST_251111） | `c36229b8…` | testkey_rsa4096 | testkey_rsa4096 | 1759622400 |
| CN ZUI 17.5.04.414（ST_260320） | `3c51d47f…` | testkey_rsa4096 | testkey_rsa4096 | 1772668800 |
| ROW ZUI 17.5.04.059（ST_260327） | `09f3c057…` | testkey_rsa4096 | testkey_rsa4096 | 1772668800 |
| ZUXOS 1.5.04.495（免解锁补充包） | `6cfa16e6…`（= 279 的 abl，按分区大小补了 0） | testkey_rsa4096 | testkey_rsa4096 | 1783209600 |
| **ZUXOS 1.5.04.495（联想官方原厂）** | `8787d92e…` | **两把**：联想自己的 RSA4096（sha1 `8fcb864f…`）+ testkey_rsa4096 | **联想自己的密钥**（vbmeta/boot/recovery/vbmeta_system 都是） | 1783209600 |

- **"495 免解锁补充包"不是原厂 495**：只有 vendor_boot、dtbo、pvmfw 和原厂 495 一样；abl 和 CN 17.0.04.279 的逐字节相同，vbmeta、vbmeta_system、recovery、boot、init_boot 都是用测试密钥重签的。
- 原厂 495 包来自联想服务资料库，有解压密码（本仓库不提供），见下面的 restore-stock。
- 原厂 495 的回滚索引是 vbmeta 0、recovery 1、vbmeta_system 1783209600、boot 1736035200。

#### 联想官方原厂 495 的 abl（`tb710fu.py abl`）

- 原厂 495 的 abl 里**两把**公钥都有，但真正用来校验 vbmeta 的是联想自己的那把，测试公钥只是第二份副本。
  证据：Freedom 安装器的 ABL 有效密钥分析（看 LinuxLoader 代码实际引用哪把）判定它的有效密钥是联想的 `7B34B551…`（sha256），
  和拯救者 Y700 四代原厂 abl 同一把；那台真机证实过，这种"带测试公钥副本"的 abl 锁着 BL 时会拒绝测试密钥签的 vbmeta。
  原厂 495 的 vbmeta、boot、recovery、vbmeta_system 也都是联想密钥签的。**这是静态分析加别的机型的实证，TB710FU 上还没直接试过（推测）。**
- 所以原厂 495 上，`check` 判为"联想原厂 495"（设备档案 `testkey: needs_abl`），刷机包会在第一阶段把**当前槽的 abl** 换成
  CN 17.0.04.279 的 abl：只认测试密钥、联想证书签名，也就是"495 免解锁补充包"里的那份，参考平板一直跑的就是它
  （锁着 BL、`verifiedbootstate=green`）。换下来的原厂 abl 在完整备份里，`restore` 会写回。
- 换用的 abl 只认这一份（补 0 到 1 MiB 后 sha256 `6cfa16e6…`，即 279 包里的 `abl.elf` `45106e3b…`）。从联想官方 CN 17.0.04.279 线刷包取：

  ```bash
  python3 tools/tb710fu.py abl TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.279_ST_250808.7z   # 也可以是解压后的文件夹或 abl 镜像
  ```

  只读，不连平板；输出 `work/abl/abl.img`。sha256 不对就拒绝。
- 启动链（`bootchain`）在原厂 495 上的差别：设备链上是联想密钥（boot、vbmeta_system 本来就重做）；recovery 不写，新 vbmeta 里
  recovery 的链式描述用**设备 recovery 自己的公钥**（联想的）；boot 是联想密钥签的，按设备回滚索引用测试密钥重签。
- `flash` 写 abl 的条件：刷机包是按 `needs_abl` 的设备档案打的；文件是上面那一份；平板上现在的 abl 正是 `check` 读到的原厂 495
  （或者已经是这一份：第一阶段中途断了重跑）；只在第一阶段、只写当前槽。

下载地址（lolinet 镜像，2026-11-11 下载；`.7z` 里是 QFIL 9008 线刷包，`image/` 下是各分区镜像和 rawprogram）：

| 固件 | 文件 | 字节数 |
|---|---|---|
| CN 17.0.04.279 | [TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.279_ST_250808.7z](https://mirrors.lolinet.com/firmware/lenowow/2025/Yoga_Tab_2025/TB710FU_CN/TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.279_ST_250808.7z) | 9055214792 |
| CN 17.0.04.354 | [TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.354_ST_251111.7z](https://mirrors.lolinet.com/firmware/lenowow/2025/Yoga_Tab_2025/TB710FU_CN/TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.354_ST_251111.7z) | 9201119510 |
| CN 17.5.04.414 | [TB710FU_CN_OPEN_USER_Q00019.0_A16_ZUI_17.5.04.414_ST_260320.7z](https://mirrors.lolinet.com/firmware/lenowow/2025/Yoga_Tab_2025/TB710FU_CN/TB710FU_CN_OPEN_USER_Q00019.0_A16_ZUI_17.5.04.414_ST_260320.7z) | 9517603070 |
| ROW 17.5.04.059 | [TB710FU_ROW_OPEN_USER_Q00019.0_A16_ZUI_17.5.04.059_ST_260327.7z](https://mirrors.lolinet.com/firmware/lenowow/2025/Yoga_Tab_2025/TB710FU_ROW/TB710FU_ROW_OPEN_USER_Q00019.0_A16_ZUI_17.5.04.059_ST_260327.7z) | 7631565787 |

ROW 17.5.04.045（ST_260116）只在 [halabtech](https://news.halabtech.com/tb710fu_row_open_user_q00019-0_a16_zui_17-5-04-045_st_260116-zip/) 见到，没下载、没检查。lolinet 是第三方镜像，包本身没有联想的校验值可比；用之前以 `check` 读出的 abl 签名证书（LENOVO）和内容为准。
大文件建议用 aria2 多连接下载（`aria2c -c -x 16 -s 16 <地址>`，`-c` 断点续传）。

这些 abl 都带联想签名证书。联想官方包自己的 vbmeta/boot 就是用 AOSP 测试密钥签的，所以"信任测试密钥"是这一代固件的正常状态；公告修的具体是什么还不清楚。

### 完整备份与取出联想原厂组件

```bash
python3 tools/tb710fu.py backup                     # 9008 只读完整备份，断了重跑会接着读
python3 tools/tb710fu.py pull                       # 从最新的完整备份取件（没有就先备份）
python3 tools/tb710fu.py pull --package 包.zip           # 平板已不是原厂系统时：从联想官方 9008 包取件（也可以给 .7z 或解压后的文件夹）
```

- **backup**：
  - 读全部 LUN 的 GPT（主表和备份表），以及除 `userdata` 和 4 GiB 及以上的非 super 分区外的所有分区，存到 `work/backup/<芯片序列号>-<时间>/`；
  - 4 GiB 及以上的非 super 分区（例如装了 Linux 后的根分区）会跳过，并列出原因；
  - 分区名不能当文件名（以 `.` 开头，或含 `/`、`\`、空字符）或带逗号（edl 会把它拆成两个名字）的分区也会跳过并列出（TB710FU 的原厂和装过 ArmadaOS 的分区表里都没有这种名字）；
  - 每读完一步就保存进度，super 按 1 GiB 一段读；
  - 结束时写 `SHA256SUMS`。里面有唯一数据分区，要另外存一份。
- **pull**：取出当前槽的组件，放到 `work/lenovo/…/`，并写一份 `lenovo.json`（sha256、build 指纹、AVB 签名和回滚索引）。
  - 取哪些：vendor、odm、vendor_dlkm、system_dlkm（system、system_ext、product 留作参考），以及启动链 boot、init_boot、vendor_boot、dtbo、recovery、vbmeta、vbmeta_system。
  - 原厂判断：只有 vendor 指纹以 `Lenovo/` 开头才算原厂，不是就拒绝。
- **官方包**：
  - 按 `rawprogram*.xml` 把 super 的各段（sparse 或 raw）拼回去，起点用包里的 `gpt_main0.bin` 定位；
  - 国行包（如 CN 17.5.04.414）的 rawprogram 是明文 xml，可以用；海外 ROW 包（如 17.5.04.059）的 xml 是加密的 `.x` 文件，
    只有联想的刷机工具能解开，不知道 super 各段的位置，所以不能用；
  - 包里的底层固件会逐个和设备档案比对，版本不一致时提示兼容风险。`pull` 和刷机（`flash`）不写底层固件；只有 `restore-stock` 会把底层固件换回联想原厂（见下文）。

### 新流程的启动链（bootchain）

```bash
python3 tools/tb710fu.py bootchain              # 默认开 dm-verity（vbmeta flags=0）
python3 tools/tb710fu.py bootchain --no-verity  # 备用：flags=1，关掉 dm-verity（作者平板之前用的启动链就是这样）
```

输入：`pull`（联想原厂启动镜像）、`vendor`、`framework` 的输出，设备档案（`check`），以及设备的完整备份（`backup`）。输出在 `work/build/bootchain/<芯片序列号>-<小米版本>/`：

| 镜像 | 怎么来的 |
|---|---|
| boot、init_boot、dtbo | 联想原厂，原样。boot 的回滚索引和设备不同时，才按设备的值重签 |
| vendor_boot | 联想原厂。解开 v4 vendor boot，在 ramdisk 的 `first_stage_ramdisk/fstab.qcom` 里给 7 个逻辑分区各加一条 erofs 条目；用同样的头部参数重新打包；用原来的 salt 和属性重加 hash footer |
| vendor、odm、vendor_dlkm、system_dlkm、system、system_ext、product | 加 dm-verity hashtree footer（sha256，不带 FEC），分区大小取能放下的最小值 |
| vbmeta | testkey_rsa4096。链式布局照抄联想原厂，并核对和设备一致：boot（位置 3，rsa4096）、recovery（位置 1，rsa4096）、vbmeta_system（位置 2，rsa2048）；另含 init_boot、dtbo、vendor_boot 的 hash 描述和 vendor 侧四个分区的 hashtree 描述 |
| vbmeta_system | testkey_rsa2048。system、system_ext、product 的 hashtree 描述，加上**设备自己的 pvmfw** 的描述（从备份取，并核对和设备当前 vbmeta_system 里记录的一致） |

- **不写的分区**：recovery、pvmfw 不写，保留设备上的（检查 recovery 是用 testkey_rsa4096 签的）。
- **回滚索引**：vbmeta 0、vbmeta_system 1783209600、boot 1736035200，都等于设备档案里的当前值，不抬高。联想 414 包自己的 vbmeta_system 是 1772668800，比设备低，所以必须重签。
- **自检**：
  - `avbtool verify_image --follow_chain_partitions`，链出去的三个分区用 `--expected_chain_partition` 指定公钥；
  - avbtool 1.3.0 会把顶层的 `--key` 也套到链出去的 vbmeta_system 上，所以 vbmeta 自己的公钥另外核对；
  - 用 CN 414 + yupei OS4.0.5.0 实测：全部通过，2 分 30 秒。vendor_boot 重新打包后，头部参数、dtb、bootconfig 和原厂完全一致，ramdisk 里只有 fstab 多了 7 行。
- **内核**：414 的内核是 6.1.128，和 vendor_dlkm 模块的 vermagic 一致。平板上的底层固件是 495 的（abl、xbl 和 414 不同，本项目不写底层固件），两者能否配合要上真机确认（推测问题不大：作者平板之前用的启动链也是 6.1.128 内核跑在 495 固件上）。

### 刷机包（package）

```bash
python3 tools/tb710fu.py framework && python3 tools/tb710fu.py framework --no-finddevice
python3 tools/tb710fu.py bootchain --framework-dir work/build/framework/<版本>
python3 tools/tb710fu.py bootchain --framework-dir work/build/framework/<版本>-nofind
python3 tools/tb710fu.py package
```

- **输出**：`work/out/<小米版本>-<芯片序列号>/`：
  - 共用的 boot、init_boot、dtbo、vendor_boot、vbmeta；
  - `super_nofind.img` + `vbmeta_system_nofind.img`：开机向导用，不带"查找设备"；
  - `super_find.img` + `vbmeta_system_find.img`：向导完成后写入；
  - `manifest.json`。
- **super**：用 `tools/make_super.py` 生成，大小取设备 GPT 里 super 的长度。只放当前槽的 7 个逻辑分区，另一个槽留空。只写到内容结束为止（约 10.7 GB，super 分区是 19 GB）。生成后读回，每个分区的字节都要和来源镜像一致。
- **一致性检查**：两份启动链除了 product 和 vbmeta_system，其余必须逐字节相同。为此构建是可重复的：hashtree 的 salt 由分区名和芯片序列号算出，EROFS 的 UUID 由版本和分区名算出，打包时间固定。
- **manifest.json**：
  - 每个文件的 sha256；
  - 输入：小米版本和指纹、联想来源和指纹；
  - 绑定的设备：芯片序列号、HWID、OEM 公钥哈希、槽、回滚索引、super 大小；
  - 刷入计划 `plan`：第一次写哪些、向导后写哪些、哪些分区永远不写。
- **实测**：CN 414 + yupei OS4.0.5.0，完整流程（两份框架侧、两份启动链、打包）13 分钟。
- **可重复**：同样的输入，两次构建出来的每个镜像（`*.img`）逐字节相同；`manifest.json` 里有构建时间（`created`），只有它会变。e2fsprogs 的时间用 `E2FSPROGS_FAKE_TIME`、`E2FSCK_TIME` 固定。

### 合入时的自动构建（#12）

`tools/ci/build.sh` 用 `TB_WORK` 里已有的输入（设备档案、备份、联想和小米组件）一口气跑完 vendor → framework ×2 → bootchain ×2 → package，任何一步的检查不过就失败。

- **跑在哪里**：自托管 runner（我的 Linux 机器），挂在私有仓库 `lich-wang/tb710fu-build` 上，不挂在这个公开仓库上，免得外人的 PR 能在那台机器上执行代码。
- **怎么触发**：本仓库的 `.github/workflows/build-check.yml` 在 push 到 `main` / `rewrite-official`、或本仓库分支提 PR 时，通知私有仓库构建这个提交。fork 来的 PR 拿不到密钥，不会触发。
- **结果**：作为 commit status `tb710fu/build` 写回提交，可以在分支保护里设为合入前必须通过。刷机包只留在 runner 机器上，不上传。
- **网络**：runner 机器的网络会断开带后量子密钥交换（X25519MLKEM768）的 TLS 握手，OpenSSL 3.5 默认会发。所以 runner 进程通过它自己的 `.env` 指定一份只用 X25519 / P-256 / P-384 的 OpenSSL 配置，不改系统设置。

### 刷入（flash）与写回备份（restore）

```bash
python3 tools/tb710fu.py flash --dry-run              # 连上平板做完所有检查、打印写入计划，不写（之后平板会复位）
python3 tools/tb710fu.py flash                        # 第一阶段：启动链 + 不带查找设备的 super，再单独确认清数据
#   → 开机向导：不要登录小米账号、不要开"查找设备"；完成后重新进 9008
python3 tools/tb710fu.py flash --stage after-setup    # 第二阶段：vbmeta_system + 带查找设备的 super，数据保留
python3 tools/tb710fu.py restore                      # 出问题时：用这台平板自己的完整备份写回启动链和 super
```

- **每次都要重新进 9008**：工具靠 9008 刚连上时打印的芯片序列号认平板。上一次运行没复位、firehose 还在跑时读不到它，工具会拒绝并提示重新进 9008。
- **写之前检查**（任何一项不过就什么都不写）：
  - 刷机包里每个文件和 `manifest.json` 记录的长度、sha256 一致；
  - 芯片序列号和刷机包绑定的一致，当前槽和刷机包的一致；
  - 刷机包的回滚索引不低于平板上现在这些镜像的（低了会启动不了）；
  - 有这台平板的完整备份：芯片序列号一致、已完成、分区表和现在一致、含全部唯一数据分区，所有文件按记录核对 sha256；
  - `flash` 的写入目标只能是当前槽的 `boot`、`init_boot`、`dtbo`、`vendor_boot`、`vbmeta`、`vbmeta_system` 和 `super`，原厂 495 再加当前槽的 `abl`（见上）（`restore` 另外写回 `recovery` 和 `abl`，见下）；按名字在现在的 GPT 里定位，名字必须只出现一次，扇区不能和唯一数据分区重叠，各个目标之间也不能重叠，长度不能超过分区；
  - 第二阶段要同时满足：这台电脑上有这个刷机包第一阶段**全部写完**的记录（`work/flash/<芯片序列号>.json`，第一阶段完全成功才写）、平板上的 `vbmeta_system` 和 super 开头 1 MiB 正是第一阶段写的那份，并且你确认开机向导已经完成；
  - 本机磁盘要放得下 super 的读回。
- **确认**：打印计划（分区 ← 文件、sha256，第一阶段还列出要清零的 `metadata` 整个、`userdata` 前 64 MiB）后要输入 `flash`；第一阶段再单独输入 `wipe` 同意清数据，第二阶段先输入 `setup-done` 确认向导已完成。所有确认都在第一次写入之前，任何一个不同意就什么都不写。`restore` 要输入 `restore`。命令行没有跳过确认的办法。
- **读回**：每个文件写之前再按 manifest / backup.json 的记录核对一次 sha256；每写一个分区立刻读回比对，不一致就停在那里，**不复位**，平板留在 9008。写完后重新读 GPT，分区表和各分区的属性（flags，决定启动哪个槽）都要和写之前一样。没有跳过读回的选项。
- **restore** 只写回启动链（含 `recovery` 和 `abl`）和 super，不碰 userdata；如果第一阶段已经清过数据，原系统开机时会自己重新初始化。备份里镜像的回滚索引低于平板上现在的（例如备份之后升级过系统）时会拒绝，因为写回去多半启动不了。
- **测试情况**：用模拟的 edl（按这台平板真实的 GPT 和备份内容建的 LUN 镜像）测过：确认、预检拒绝的各种情况、写坏后读回发现、第一、二阶段、写回备份（含 19 GB 的 super）。**还没在真机上写过。**

### 恢复联想官方原厂（restore-stock，#37）

```bash
python3 tools/tb710fu.py restore-stock --dry-run                       # 下载/核对/解压、连上平板做完所有检查、打印计划，不写
python3 tools/tb710fu.py restore-stock                                 # 恢复成联想官方原厂 ZUXOS 1.5.04.495
python3 tools/tb710fu.py restore-stock --package TB710_ZUXOS_1.5.04.495_Tool.7z   # 用已经下载好的包
```

**正常情况下用 `restore`**（用这台平板自己的备份写回）。`restore-stock` 是兜底：`restore` 用不了（例如分区表已经变了），或者想从原厂系统重新开始时用。它也要先有这台平板的完整备份（芯片序列号一致、核对通过；分区表可以和现在不同）：`backup` 在 9008 下做，安卓坏了也能做。
完整备份**不含** userdata（安卓数据）和 4 GiB 及以上的非 super 分区（例如 ArmadaOS 的 ARMADA_ROOT）：这些数据恢复后就没有了，要留的请事先自己拷出来（安卓里的文件可以用系统备份或 adb 拷到电脑）。
原厂包里的 persist 等只是空白模板，平板自己的这些数据只能靠备份找回。

- **原厂包**：联想服务资料库的 `TB710_ZUXOS_1.5.04.495_Tool.7z`（10156610508 字节）。地址、大小、sha256 写在 `tools/tb/stock_495.json`，工具会断点续传下载并核对整包。
- **解压密码**：包有解压密码，**本仓库不提供**。运行时工具会让你输入，也可以设置环境变量 `TB_STOCK_PASSWORD`。密码只传给 7-Zip，不打印、不保存。解压后 100 个文件逐个按 `stock_495.json` 核对 sha256。
- **写什么**：
  - 照联想自己刷机脚本用的清单（`rawprogram_unsparse0-half.xml` + `rawprogram1–5.xml`，本来就不写 persist），写原厂的底层固件、A 槽启动链、super、metadata、userdata；
  - 再去掉唯一数据分区：联想的清单还会写 `secdata`（和设备上的相同）和 `ddr`（清掉内存训练数据），这里都不写；
  - 最后像联想脚本一样把启动盘设为 LUN1（xbl_a 所在）。
- **分区表**：
  - 每个 LUN 的 GPT 按包里的 `patch*.xml`、用这个 LUN 的实际大小离线算好（userdata 一直到盘尾，主、备两份，CRC 核对）；
  - 和设备上一样的不写。不一样的，只有在**所有唯一数据分区的位置和大小都与设备上完全相同**时才写，否则什么都不写、提示求助；
  - 写完分区表后重新读出核对。
  - 原厂分区表里两个槽都没有标"当前"，和联想自己刷机一样，开机时由 abl 选 A 槽。
- **写之前**：
  - 芯片序列号（Sahara）；
  - 有这台平板的完整备份（芯片序列号一致、已完成、含全部唯一数据分区、所有文件按记录核对 sha256；默认最新一份，`--backup` 指定）；
  - 平板上镜像的回滚索引不能比原厂 495 高（高了原厂启动不了，拒绝）：两个槽的 vbmeta 读到就比；vbmeta 链到的 boot、vbmeta_system 再逐项读、读到就比，任一项超标就拒绝。某槽的 vbmeta 读不到时，这个槽的链式分区就比不了；
    vbmeta 没链到 boot 或 vbmeta_system 时也比不了——这几种情况都只提示（系统坏了时常见）。
    bootloader 自己存的回滚计数在 RPMB 里（TEE 管），9008 读不到；它只会升到验证通过并启动过的镜像的索引，所以两个槽的镜像是这里能拿到的证据，已经不在任何一个槽里的更高版本看不到——所以这项检查过了，恢复后仍有可能启动不了（这也是为什么要先有完整备份）；
  - 打印全部写入项（分区 ← 文件、大小、完整 sha256），先输入 `stock` 确认，再输入 `wipe` 同意清数据；
  - **第一次写入前，自动只读保存**全部 LUN 的主、备份 GPT 和所有唯一数据分区到 `work/stock-safety/<芯片序列号>-<时间>/`（附 SHA256SUMS）。
- **读回**：每次写入都读回比对，不一致立刻停下，平板留在 9008。
- **后果**：安卓数据清空；原厂以外的分区（例如 ArmadaOS 的 ARMADA_BOOT/ROOT/BACKUP）删掉，userdata 恢复原厂大小；底层固件和 A 槽启动链换回原厂。B 槽不写。
- **适用范围**：TB710FU 国行原厂 495（目前联想服务库里最新的版本）。设备如果已经装了更新的系统（回滚索引更高），会被拒绝。海外版（ROW）机器刷国行固件没有验证过。
- **测试情况**：
  - 模拟的 edl（按这台平板真实的 GPT 和全部备份分区建的 6 个 LUN）：完整恢复（唯一数据分区逐字节不变、LUN0 恢复原厂、ARMADA 分区删除、userdata 到盘尾、abl/boot/xbl/vbmeta_system 为原厂、启动盘 LUN1）、重复执行、唯一数据分区位置不同时拒绝、回滚索引更高时拒绝、密码错误。
  - **真机（2026-11-23，参考平板）**：从“HyperOS 4（旧方案）+ ArmadaOS Freedom 双系统”恢复。六个 LUN 的分区表、A 槽 73 项全部写入并读回一致，重新读出的分区表和算好的一致；
    复位后直接进联想原厂开机向导：`ZUXOS_1.5.04.495_ST_260728`，`verifiedbootstate=green`、BL 仍锁、当前槽 A；ARMADA 分区没有了，`/data` 215 GB（userdata 到盘尾）。
    同时发现并修好：`edl setbootablestoragedrive` 不接受 `--memory`（之前写完所有分区后卡在设启动盘这一步，平板留在 9008，补发后正常）。
    之后又两次从“原厂 495 + Freedom 双系统”恢复（A 槽 + 分区表 68 项、`--slot-b` 33 项，全部读回一致），每次开机都是原厂 495、`green`、槽 A。
- **B 槽**：联想的清单只写 A 槽。`restore-stock --slot-b` 把 A 槽清单里每一项的同一个原厂文件写到对应的 `_b` 分区（底层固件 + 启动链，32 项），
  要求分区表已经是原厂的（先跑一次不带它的 restore-stock）；不写分区表、super、metadata、userdata，不清数据，槽位属性不变（仍从 A 槽启动）。确认词 `stock`。
- **已经解压过**：`work/stock/1.5.04.495/image` 里的 100 个文件全部核对通过时不再解压，也不要密码（可以把以前解压好的 `image` 文件夹放到这里）。

## 9008（EDL）

- 进入：`adb reboot edl`（需 root 或在 Android 下），或者拔线 → 长按电源 15–20 s 关机 → 按住音量+和音量−插线。
- 工具：[bkerler/edl](https://github.com/bkerler/edl) + 该机型的 firehose programmer（刷机包 `bin/9008/prog_firehose_ddr-TB710FU.melf`）。
  ```
  edl printgpt --lun=0 --loader=prog_firehose_ddr-TB710FU.melf --memory=ufs
  edl r boot_a boot_a.img --loader=... --memory=ufs
  ```
- **先备份**：所有 LUN 的 GPT、`persist`、`modemst1/2`、`fsg`、`fsc`、`devinfo`、`frp`、`keystore`、`ssd`、
  `secdata`、`uefivarstore`、`cdt`、`ddr`，以及 A 槽的 `abl boot init_boot vendor_boot dtbo vbmeta vbmeta_system recovery pvmfw`。
  唯一数据分区丢了没有任何地方能找回（IMEI、校准数据、密钥）。

## 900E：内核崩溃转储模式

设备 USB 枚举为 `05C6:900E`（Qualcomm HS-USB Diagnostics）时，说明内核 panic 后进入了 Sahara 内存转储。
`edl` 连上会识别出来并导出转储；在 `md_KBOOT_LOG.BIN` / `md_KLOGBUF.BIN` 里 `strings` 找 panic 前的日志，
就能看到真正的崩溃原因。调试时可以给 `vendor_boot` 的 bootconfig 加 `androidboot.init_fatal_panic=true`，
让 init 的致命错误也进 900E，便于抓日志。

## 背景：为什么 vendor_boot 要补 EROFS、内核要和模块配套

这两条是作者平板早期刷第三方移植包时进 900E 得到的教训，新流程的 `vendor` / `bootchain` 已经按此处理：

- 联想原厂 `vendor_boot` 的 first-stage fstab 对 system / vendor / product 等逻辑分区**只写了 ext4**，而小米的 super 全是 **EROFS**。
  不补 erofs 条目，init 挂不上 system，内核 panic（`Invalid ext4 superblock` → `Failed to mount /system` → `Attempted to kill init!`）。
- boot 的内核要和 `vendor_dlkm` / `system_dlkm` 里模块的版本一致，否则卡开机动画。新流程的启动链和 dlkm 都取自同一个联想版本。
- `metadata` 分区被清零后不是有效 f2fs，首次开机会自动格式化，属正常。
