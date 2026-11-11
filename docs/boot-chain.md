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
AOSP `mkbootimg` / `unpack_bootimg`。仓库不附带它们，放到 `tools/avb/`、`tools/mkbootimg/` 即可。

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
- **输出**：写入设备档案 `work/device/latest.json`，后面的构建和刷入以它为准。
- **9008 方式**：还会核对芯片 HWID 和 OEM 公钥哈希，检查完自动复位开机（`--no-reset` 留在 9008）。

联想公告 LEN-221035（CVE-2026-92777）把海外版 TB710FU 的修复版本定为 **ZUI 17.5.04.059**，但版本号不能代替实际读 abl：
实测公告所说的修复版 ROW 059 的 abl 里，内置的仍然只有 `testkey_rsa4096`。实际读过的联想官方固件：

| 固件 | abl sha256 | abl 内置根公钥 | 官方包 vbmeta 签名 | 官方 vbmeta_system 回滚索引 |
|---|---|---|---|---|
| CN ZUI 17.0.04.279（ST_250808） | `45106e3b…` | testkey_rsa4096 | testkey_rsa4096 | 1749081600 |
| CN ZUI 17.0.04.354（ST_251111） | `c36229b8…` | testkey_rsa4096 | testkey_rsa4096 | 1759622400 |
| CN ZUI 17.5.04.414（ST_260320） | `3c51d47f…` | testkey_rsa4096 | testkey_rsa4096 | 1772668800 |
| ROW ZUI 17.5.04.059（ST_260327） | `09f3c057…` | testkey_rsa4096 | testkey_rsa4096 | 1772668800 |
| ZUXOS 1.5.04.495（免解锁补充包） | `6cfa16e6…` | testkey_rsa4096 | — | 1783209600 |

这些 abl 都带联想签名证书。联想官方包自己的 vbmeta/boot 就是用 AOSP 测试密钥签的，所以"信任测试密钥"是这一代固件的正常状态；公告修的具体是什么还不清楚。

### 完整备份与取出联想原厂组件

```bash
python3 tools/tb710fu.py backup                     # 9008 只读完整备份，断了重跑会接着读
python3 tools/tb710fu.py pull                       # 从最新的完整备份取件（没有就先备份）
python3 tools/tb710fu.py pull --package 包.zip|.7z|文件夹   # 平板已不是原厂系统时：从联想官方 9008 包取件
```

- **backup**：
  - 读全部 LUN 的 GPT（主表和备份表），以及除 `userdata` 外的所有分区，存到 `work/backup/<芯片序列号>-<时间>/`；
  - 超过 4 GiB 的非系统分区（例如装了 Linux 后的根分区）会跳过，并列出原因；
  - 每读完一步就保存进度，super 按 1 GiB 一段读；
  - 结束时写 `SHA256SUMS`。里面有唯一数据分区，要另外存一份。
- **pull**：取出当前槽的组件，放到 `work/lenovo/…/`，并写一份 `lenovo.json`（sha256、build 指纹、AVB 签名和回滚索引）。
  - 取哪些：vendor、odm、vendor_dlkm、system_dlkm（system、system_ext、product 留作参考），以及启动链 boot、init_boot、vendor_boot、dtbo、recovery、vbmeta、vbmeta_system。
  - 原厂判断：只有 vendor 指纹以 `Lenovo/` 开头才算原厂，不是就拒绝。
- **官方包**：
  - 按 `rawprogram*.xml` 把 super 的各段（sparse 或 raw）拼回去，起点用包里的 `gpt_main0.bin` 定位；
  - 国行包（如 CN 17.5.04.414）的 rawprogram 是明文 xml，可以用；海外 ROW 包（如 17.5.04.059）的 xml 是加密的 `.x` 文件，
    只有联想的刷机工具能解开，不知道 super 各段的位置，所以不能用；
  - 包里的底层固件会逐个和设备档案比对，版本不一致时提示兼容风险。本项目不写底层固件。

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

## 本机的救砖与"v3 启动链"

起点：HyperOS 3 移植包（酷安 @兰微卡鱼，底包为红米平板 2 Pro）刷完后进 900E，转储显示：

```
init: [libfs_mgr] Invalid ext4 superblock on '/dev/block/dm-2'
init: Failed to mount /system: File exists
Kernel panic - not syncing: Attempted to kill init!
```

原因：免解锁补充包为了过防回滚，把整套启动链换成了 ZUI 495 的镜像；但 495 的 `vendor_boot` 里
first-stage fstab 对 system/vendor/product 等逻辑分区**只写了 ext4**，而 HyperOS 的 super 全是 **EROFS**。
单纯在 fstab 里加 erofs 条目后不再 panic，但又卡开机动画——495 的 boot 内核（6.1.157）与 super 里
system_dlkm/vendor_dlkm 的模块（6.1.128）版本不一致。

最终可用的 **v3 启动链**：
- `boot`、`init_boot`、`dtbo`、`vendor_boot` 用移植作者原版（内核 6.1.128，与模块匹配；fstab 每个逻辑分区 ext4+erofs 两条）。
  原版 `vendor_boot` 的 footer 摘要是旧的：`erase_footer` 后重新 `add_hash_footer`。
- 新 `vbmeta`：`testkey_rsa4096`，flags=1，chain `boot`/`recovery`/`vbmeta_system`。
- `vbmeta_system`、`pvmfw`、`recovery` **保留设备上 495 的版本**，以满足防回滚。

其他注意点：
- `metadata` 分区被清零过时不是有效 f2fs，首次开机会自动格式化，属正常。
- 刷机包里 `misc.img` 带 BCB `bootonce-bootloader`（首次启动进 fastboot），按需处理。
