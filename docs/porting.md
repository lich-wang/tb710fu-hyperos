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
   - `vendor/etc/media_profiles*.xml` 都在，所以不用再注掉 `media.settings.xml`。
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
| vendor target-level | 8（`vendor/etc/vintf/manifest_pineapple.xml`，SM8650 的 SKU） | `vintf_check.py` |
| 框架支持的 FCM | 7、8、202404、202504、202604 | 同上 |
| 原厂 vendor 的 `android.*` HAL 版本是否被 FCM 8 接受 | 29/29 | 同上 |
| 原厂 vendor 的设备兼容性矩阵要求的框架服务 | 缺 HIDL 的 `android.frameworks.sensorservice@1.0`、`android.hidl.memory`（Android 17 已去掉 HIDL 版本）；只影响 OTA 时的 VINTF 校验，开机不检查（推测，待真机） | 同上 |
| SELinux：原厂 vendor + yupei 框架 | **编译通过**（不加补丁） | `sepolicy_compile.sh len_` |
| SELinux：移植包 vendor + yupei 框架 | 不加补丁失败（`vendor_sepolicy.cil:18860`），加 `sepolicy_compat.cil` 才通过，和平板现状一致，可以作为对照 | `sepolicy_compile.sh port_ …` |
| VNDK v34 | yupei `system_ext/apex/com.android.vndk.v34.apex` | — |
| media_profiles | 原厂有 `media_profiles.xml`、`media_profiles_pineapple.xml` 等 | — |

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
| `etc/init/init.spdaemon.rc`、`bin/init.qcom.post_boot.sh` | `ro.product.device`、`ro.product.name` 改成读 `lwky.hyper.*` | **要做，换个方式**：product 把机型改成小米后，`ro.product.device` 不再是 `pineapple`，高通脚本会走错分支。直接写死 `pineapple`（这份 vendor 只跑在 TB710FU 上），不引入新属性 |
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

product 侧的其他改动（`pagesize.max`、`lcd_density`、device_features、手写笔、电池属性、mi_ext 合并、查找设备、人脸）在 `tools/build_os4.py` 里，属于 #7。

## 下一步

- **#6（vendor/odm）**：联想原厂 vendor 加三处改动——fstab 加 erofs、高通脚本写死 `pineapple`、去掉坏的 rc 行。第一版不加小米 HAL。
- **#7（框架侧）**：去掉 `sepolicy_compat.cil` 和 `media.settings.xml` 的改动；lwky 只保留 `/proc` 那几段；小米机型身份放在 product。
- **#8（启动链）**：vendor_boot 的第一阶段 fstab 加 erofs；保留 `avb=`，按设备的回滚索引重签。
- **#11（真机）**：逐项确认色彩风格、充电、手写笔、皮套、相机/视频、小米账号等功能；缺哪个 HAL 再按上面的办法从 yupei 加，并给它写最小 SELinux 策略。
