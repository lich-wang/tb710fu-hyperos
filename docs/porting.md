# 联想原厂 vendor 跑小米框架：需要补什么（#5 研究结论）

> 2026-11-11。比较对象：
> - **联想原厂**：CN ZUI 17.5.04.414 官方 9008 包里的 vendor、odm、vendor_dlkm、system_dlkm，用 `tb710fu.py pull --package` 取出；
> - **移植包**：酷安 HyperOS 3.0.305 移植包的 vendor、odm、dlkm，也就是现在 HyperOS 4 方案里用的那份；
> - **yupei**：小米平板 8 `OS4.0.5.0.XPZCNXM` 官方包里的 system、system_ext、product、mi_ext、vendor、odm，用 `tb710fu.py import` 取出。
>
> 方法：在 Linux 上只读挂载，用 `tools/research/` 里的脚本比较（用法见各脚本开头）。下面凡是没在真机上验证过的，都标明了"推测"或"待真机"。

## 结论

1. **联想原厂 vendor 和小米 Android 17 框架在离线检查上是兼容的**：
   - VINTF：原厂 vendor 的 target-level 是 8，它的 29 个 `android.*` HAL 版本都在框架 FCM 8 的允许范围内；
   - SELinux：原厂 vendor 的策略和 yupei 框架的策略直接编译通过，**不需要** `hyperos/sepolicy_compat.cil`；
   - VNDK：vendor API 是 34，SELinux 版本 34.0；yupei 框架带有 `com.android.vndk.v34.apex` 和 `mapping/34.0.cil`；
   - 例外：小米 system 的 `media.settings.xml=/vendor/etc/media_profiles_vendor.xml` 指向的文件，联想原厂 vendor 里没有（只有 `media_profiles.xml` 等），zygote 会因此 abort，所以这一行仍然要注掉（#7）。
2. **移植包的 vendor 就是联想原厂 vendor**，底子是 1.5.04.428：
   - 3645 个文件里有 3566 个和 414 原厂逐字节相同，原厂的文件一个没少；
   - 改动只有几十个文件（见下面"差异清单"）。
3. 移植包里的小米 HAL 二进制**来源不明**：
   - 它们和 yupei 的不同，版本和接口倒是一样；
   - vendor 被改成了 `Xiaomi/mivendor_pad8650_cn … OS3.0.301.0.WOYCNXM`，odm 来自 piano（`OS3.0.305.0.WPWCNXM`）。
   - 按"只用官方来源"的原则，**不用它们**。
4. 如果需要小米 HAL，可以从 yupei 官方 vendor 取，依赖库都找得到。但其中 4 个服务用到了比 VNDK v34 更新的 libc++ 符号，要连同 yupei 的 `libc++.so` 一起改名带上（见"小米 HAL"一节）。**先做一个不带任何小米 HAL 的构建，上真机看缺了什么，再按需加**。

## 兼容性检查（离线）

| 检查 | 结果 | 怎么查 |
|---|---|---|
| vendor target-level | 8（`vendor/etc/vintf/manifest_pineapple.xml`，SM8650 的 SKU） | `vintf_check.py mnt len_ xm_ pineapple`，第 1 项 |
| 框架支持的 FCM | 7、8、202404、202504、202604 | 同一条命令，第 1 项（`xm_system/system/etc/vintf/compatibility_matrix.*.xml`） |
| 原厂 vendor 的 `android.*` HAL 版本是否被 FCM 8 接受 | 29/29 | 同一条命令，第 2 项 |
| 原厂 vendor 的设备兼容性矩阵要求的框架服务 | 缺 HIDL 的 `android.frameworks.sensorservice@1.0`、`android.hidl.memory`（Android 17 已去掉 HIDL 版本）；开机时 libvintf 也会做这项匹配，缺项不影响启动（参考平板真机开机正常） | 同一条命令，第 3 项（`len_vendor/etc/vintf/compatibility_matrix.xml` 对 yupei 的 system / system_ext / product manifest） |
| SELinux：原厂 vendor + yupei 框架 | **编译通过**（不加补丁） | `sepolicy_compile.sh len_` |
| SELinux：移植包 vendor + yupei 框架 | 不加补丁失败（`vendor_sepolicy.cil:18860`），加 `sepolicy_compat.cil` 才通过，和平板现状一致，可以作为对照 | `sepolicy_compile.sh port_ …` |
| VNDK v34 | yupei `system_ext/apex/com.android.vndk.v34.apex` | — |
| media_profiles | 原厂有 `media_profiles.xml`、`media_profiles_pineapple.xml` 等，但**没有** system 指定的 `media_profiles_vendor.xml` | — |

Ubuntu 的 secilc 3.9 不认识 Android 17 新加的 `(policycap functionfs_seclabel)`，编译前要先去掉这一行，脚本里已经处理了。

## 差异清单（移植包 vs 联想原厂）

`vendor_diff.py` 按内容逐个比对：

| | vendor | odm |
|---|---|---|
| 和原厂相同 | 3566 | 10 |
| 路径相同、内容不同 | 43 | 1（build.prop） |
| 只有移植包有 | 33 | 25 |
| 和 yupei 相同 | 3 | 11 |

"路径相同、内容不同"的 43 个文件里，相机库、IMS、firmware、`audio.primary` 这类多半是 428 和 414 的版本差，不是作者改的（推测）。确定是作者改的有：

| 文件 | 改动 | 处理 |
|---|---|---|
| `etc/fstab.qcom` | 每个逻辑分区加一条 erofs；system、vendor 等去掉了 `avb=` | **要做**：加 erofs 条目。`avb=` 保留，因为我们会用 testkey 给 vbmeta_system 和新镜像重签（#8），不需要关校验。vendor_boot 里的第一阶段 fstab 也要同样改（#8） |
| `build.prop` | vendor 身份改成 `Xiaomi/mivendor_pad8650_cn` | **不改**：vendor 保持联想身份，小米机型身份放在 product 的 build.prop（#7）。小米框架是否读 `ro.product.vendor.*` 待真机 |
| `etc/init/init.spdaemon.rc`、`bin/init.qcom.post_boot.sh` | `ro.product.device`、`ro.product.name` 改成读 `lwky.hyper.*` | **不要**。原厂系统上这两个属性的值就是 `TB710FU`：`post_boot.sh` 比较的是 msmnile/kona/msm8952 等别的平台名，`init.spdaemon.rc` 判断的是 `ro.product.device=pineapple`，原厂本来就都不匹配。换成小米的机型名，结果和原厂一样。作者的改动反而让 spdaemon 那段在原厂不执行的情况下执行了 |
| `etc/init/vendor.lenovo.hardware.keyboard.rc` | 多出一行 `eabled`（拼写错误，init 会报错跳过） | 不要 |
| `etc/selinux/*` | 合并了小米 mivendor 的整套策略（`vendor_sepolicy.cil` 里和小米相关的规则，原厂 54 行，移植包 1197 行） | **不要整套**。只给真正加进来的小米 HAL 写最小策略（类型、`service_contexts`、`file_contexts`、必要的 allow） |
| `etc/selinux/vendor_mac_permissions.xml` | 换成 yupei 的 | 不要（推测只和小米 vendor 应用的签名有关，待真机） |
| odm `etc/build.prop` | 加了小米的媒体、视频、显示属性（`debug.config.media.video.*`、`persist.vendor.audio.effectimplenter=dolby` 等） | 先不要；真机上相机、视频功能不对时再逐条加 |

## 小米 HAL

移植包加进来的，以及 yupei 里的对应版本：

| HAL | 移植包 | yupei | 作用 | 处理 |
|---|---|---|---|---|
| `vendor.xiaomi.hardware.displayfeature_aidl` V2（+ `bin/displayfeature`、`libdisplayfeature*`、`libtflite` 等） | 有 | V2 | 小米"色彩风格"、阅读模式等，`DisplayFeatureManagerService` 用它 | 先不加，待真机；需要时从 yupei 取 |
| `vendor.xiaomi.hardware.misys.core` V1、`misys.common` V3 | 有 | 有 | 小米系统服务的文件操作通道（推测：备份、主题等会用） | 先不加，待真机 |
| `vendor.xiaomi.hardware.micharge` V2 | 有（被 `init.lwky.rc` 的 `micharge-hal_lwky` 启动） | V2 | 电池、充电信息和小米"智能充电" | 先不加，待真机 |
| `xiaomi.system.hypsys`（`bin/hypsys_vendor`）+ `blackbox` | 有 | 有 | 系统诊断、日志 | 不要（推测非必需） |
| odm：`mlipay`、`mfidoca`、`vsimapp`、`aidl.tidaservice`、`aidl.mtdservice` | 有 | 有 | 小米支付、FIDO、虚拟 SIM、设备身份；都依赖小米 TEE 里的 TA，联想固件上没有 | **不要**：放进来也用不了（查找设备就是因为 mtdservice 拿不到 fid 才不能用） |
| odm：`mslgservice` + `assets/rootfs-25.09.04.tgz` + `start-rootfs.sh` 等 | 有 | `mslgusrimg`/`md5.txt` 有 | 推测是小米的 Linux 子系统（容器） | 不要 |
| `lib_bt_lhdc.so`、`lib_misound_asc.so`、`libEseUtils.so`、`libesesbprovision.so` | 有 | — | 蓝牙 LHDC、小米音效、eSE | 不要（推测非必需） |

### 从 yupei 取 HAL 的可行性（`hal_deps.py`）

- **库依赖**：yupei 的 `displayfeature_aidl-service`、`displayfeature`、`micharge-service`、`misys.core-service`、`hypsys_vendor`，在"联想 vendor + VNDK v34 + LLNDK"下都能找到，缺的只是 yupei 自己的几个接口库（`vendor.xiaomi.hardware.*-ndk.so`、`libdisplayfeature*`、`libtflite`、`libmisight` 等），一起带上即可。
- **符号**：yupei 的 vendor 是按 API 202404（Android 15）编译的，比 VNDK v34 的 libc++ 新：
  - `bin/displayfeature`：0 个找不到的符号；
  - `displayfeature_aidl-service`、`misys.core-service` 缺 `std::__1::__libcpp_verbose_abort`；
  - `micharge-service`、`hypsys_vendor` 还缺 `basic_filebuf::open` 等 5–8 个符号。
  - 原样放进去，这几个服务会在动态链接时失败（推测，待真机）。
- **办法**：yupei 的 vendor 自带一份 `libc++.so`（有这些符号）。把它改名一起带上：用等长字符串改 SONAME，例如 `libc++.so` → `libcxx.so`，并改那几个二进制的 NEEDED。只改动态段里的库名，不动代码。改过的二进制在构建清单里标明原始 sha256 和改动。

## 移植作者的系统改动（lwky）

来自现在平板上的 system（`init.lwky.rc` + `system/lwky/`）：

| 内容 | 作用 | 处理 |
|---|---|---|
| `set_res.sh`（`cmd window size 2000x3198`） | 把分辨率改成 2000×3198 | **去掉**（已知原生 3200×2000 没问题） |
| `wmhp.sh` + `wmhp/*.xml` | 把窗口规则（平行视界、固定方向、自动 UI 列表）写进 `/data/system/cloudFeature_*` 并 `chattr +i` 锁住 | 先去掉；平板应用显示有问题时，再用自己的 overlay 或数据文件实现，不锁文件 |
| `lwky_smart_charge_hyper` | 按电量和屏幕状态写 `/sys/class/power_supply/battery/charge_control_limit` 和 `charging_enable` | **去掉**。这多半就是之前 Linux 下看到的"只充到 80%"（推测）。充电上限以后用小米的 micharge 或设置项来做 |
| `lwky_cover` | 静态链接的小程序，读 `hall_irq` 和 `persist.sys.device_case_enabled`，按皮套开合控制背光 | 待真机：先看联想 vendor 自己有没有处理皮套。需要的话自己写，不用来历不明的二进制 |
| `on property:*` 那几段 chmod/write（`/proc/gesture_control`、`/proc/support_pen`、`/proc/game_mode`，以及 `/sys/class/xm_power/*`） | 把小米设置项（双击唤醒、手写笔、高触控采样率）接到联想内核的节点上 | **保留并重写**：`/proc/*` 那几段是真实功能（手写笔开关等）；`xm_power` 在联想内核上不存在，去掉 |
| `start micharge-hal_lwky` | 启动 micharge HAL | 跟着 micharge 的决定走 |
| `lwky.hyper.*` 属性 | 机型伪装，供 vendor 脚本读取 | 不要（vendor 那边改成写死，见上） |

product 侧的其他改动（`pagesize.max`、`lcd_density`、device_features、手写笔、电池属性、mi_ext 合并、查找设备、人脸）由 `tb710fu.py framework` 处理，见下面 #7。

## 下一步

- **#6（vendor/odm）✅**：`tb710fu.py vendor`。
  - vendor 只改 `etc/fstab.qcom` 一处：每个逻辑分区加一条 erofs 条目，`avb=` 保留；
  - 做法是用 e2fsprogs 原地修改 ext4（先 `e2fsck -E unshare_blocks` 去掉 Android 的块去重，再用 debugfs 替换文件，保留权限、属主和 SELinux 标签，最后收缩）；
  - odm、vendor_dlkm、system_dlkm 原样；四个镜像都去掉了 AVB footer，由构建重签；
  - 用 CN 414 实测：3820 个条目里只有 fstab 不同，`e2fsck -fn` 干净，vendor 从 1.8 GB 变成 2.0 GB（拆开共用块后多出约 250 MB）；
  - 第一版不加小米 HAL。
- **#7（框架侧）✅**：`tb710fu.py framework [--no-finddevice] [--debug]`，用 erofs-utils 解包再重新打包（每个文件的 uid、gid、mode 和 SELinux 标签都取自小米原镜像，新加的文件指定从哪个文件取）。
  - **system**：
    - `build.prop` 注掉 `media.settings.xml`（联想 vendor 没有 `media_profiles_vendor.xml`，zygote 会 abort）；
    - 加 Lenovo Tab Pen Plus 按键映射；
    - 加磁吸皮套按键映射 `hall_irq.kl`（#54）：霍尔传感器 `hall_irq` 合 / 开时发的是按键 252 / 253，不是 `SW_LID`，安卓的合盖休眠收不到；映射成 `SLEEP`（合上）/ `WAKEUP`（打开）。合 / 开对应哪个码按 `getevent` 抓到的顺序推断，真机核对；
    - 加 `etc/init/tb710fu.rc`：手写笔、双击唤醒、高触控采样率接到联想触摸驱动的 `/proc` 节点，lwky 的其余部分都不要；
    - `product/pangu/system` 和 mi_ext 的系统权限文件移进来。小米 vendor 开机时会把它们叠上去，联想 vendor 不会；com.tms.* NFC jar 不带。
  - **system_ext**：不改，直接复制。
  - **product**：
    - mi_ext/product 合并进来，原因同上；
    - `build.prop`：`pagesize.max=4096`（联想内核是 4K 页），去掉 `lcd_density`，并入 mi_ext 的属性，`.DEV`（小米账号需要，`--no-finddevice` 时不加）、手写笔、skiagl；
    - 机型身份：联想 odm 会把机型报成 `TB710FU`（system 的属性顺序是 odm 优先），小米框架就找不到 `device_features/TB710FU.xml`。所以把 `ro.product.property_source_order` 改成 product 优先，并写入 yupei odm 的身份（Xiaomi / yupei / 25097RP43C）。联想 odm 的 `*_for_attestation` 不动，要和设备出厂写入的身份一致；
    - 两个 NCM overlay 在 `work/overlay/` 下有就带上；
    - **小米相机前摄（#48）**：小米相机只按小米的 vendor tag `com.xiaomi.cameraid.role.cameraIds` / `.cameraId` 建“角色 → 摄像头”表，
      联想 CamX 不发布这两个 tag（配置层也加不了），表里只有角色 -1，前摄（角色 1）解析成摄像头 -1，切前摄就“无法连接到相机”。
      HAL 本身正常：摄像头 0 = 后摄主摄 txd_ov13b10，1 = 前摄 lce_gc13a2（选前摄时内核有 `CAM_START_DEV Success for lce_gc13a2`）。
      `framework` 把官方 `priv-app/MiuiCamera/MiuiCamera.apk` 的 dex 交给 apktool 改两处（`tools/tb/miuicamera.py`，按日志字符串 / 未混淆的类名定位，
      定位不到或不唯一就停下）：① 角色表建好时补上缺的“角色 1 → 摄像头 1”“角色 0 → 摄像头 0”（HAL 自己发布了就不动，访问器不改）；
      ② `MIVILutSaver.testPermission()` 往小米的 persist 目录写测试文件，联想 persist 结构不同必然失败并弹“权限受限，请联系研发”，
      persist 又不能写，所以这一处当作通过。只有 dex 经过 apktool：小米的 APK 里有 119 对只差大小写的资源名（`res/-KN.xml` 和 `res/-kn.xml` 等），
      在 macOS 不区分大小写的磁盘上整包解开会丢一半，相机一打开就崩（真机踩过）。所以结果是原 APK 逐条复制、只换打了补丁的 dex，
      再 `zipalign`、用 AOSP 测试密钥（`tools/apksign/`）签名，并核对条目和原 APK 一样；去掉按原 dex 编译的 `oat/`。
      MiuiCamera 没有 `sharedUserId`，重签后仍作为 priv-app 运行。真机（2026-11-24）：前摄正常；请求的 77 项权限里只有平台签名权限
      `CONTROL_DEVICE_STATE` 没给（小米原来的签名也拿不到），小米自己的图库、安全中心、省电、媒体资产等权限都在。
      代价：签名变了，应用商店推送的小米相机更新会因签名不符装不上——相机要随本项目的刷机包升级（#50）。
      思路来自旧移植包的 `tb710fu_camfix_v1` 模块（它改的是另一个版本的 APK，这里不带它）。
    - **去掉系统自带更新 `app/Updater`（#49）**：它把平板当成小米平板 8（yupei），会下载 yupei 的官方 OTA（带 SM8750 的底层固件和 vendor），
      在这台平板上要么校验失败，要么把不匹配的固件写进另一个槽。升级改走本项目。`system_ext/bin/miupdater`（只在
      `persist.sys.cota.partition` 被设置时，往 `opcust` / `opconfig` / `md1img_a` 写运营商定制包）没去掉：这几个分区 TB710FU 上都没有。
  - 不再需要 `sepolicy_compat.cil`、移植包的 PearlBiometric 人脸，以及 lwky 的分辨率、窗口规则、智能充电、机型伪装。
  - OS4.0.12.0（2026-11-03，和 4.0.5.0 同一个 Android 基线 `CP2A.260605.016`，只换了小米自己的增量）：`framework` 不用改代码。
    小米相机的混淆类名变了（角色表 `r6.1/e` → `s6.1/d`，persist 自检 `E1/Y0` → `F1/R0`），按日志字符串 / 未混淆类名定位仍各找到唯一一处；
    `app/Updater` 位置没变。真机（装着 Freedom，`flash --stage system`）进桌面正常，`verifiedbootstate=green`。
  - OS4.0.5.0 实测（3 分 36 秒）：和小米原镜像逐项比对，system 只有 `build.prop` 内容变了、新增 10 个条目；product 只有 `etc/build.prop` 变了，删掉 pangu（24 个条目）、新增 mi_ext 的 139 个条目。所有共有路径的元数据都不变，新增条目都有指定的元数据。
  - 待真机：人脸解锁（yupei 声明的是 TEE 人脸，这里没有）、`device_features/yupei.xml` 里的 144Hz 等参数和这块屏是否相符。
- **#8（启动链）✅**：`tb710fu.py bootchain [--no-verity]`，详见 [boot-chain.md](boot-chain.md#新流程的启动链bootchain)。
- **#11（真机）**：逐项确认色彩风格、充电、手写笔、皮套、相机/视频、小米账号等功能；缺哪个 HAL 再按上面的办法从 yupei 加，并给它写最小 SELinux 策略。
