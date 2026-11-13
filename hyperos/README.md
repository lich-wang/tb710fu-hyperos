# HyperOS 侧文件（`tb710fu.py framework` 用到）

| 路径 | 说明 |
|---|---|
| `keylayout/Vendor_17ef_Product_617f.kl` | Lenovo Tab Pen Plus（BLE HID 17EF:617F）按键映射：单击 0x0C0600 → BACK、双击 0x0C0601 → SYSRQ（截屏）、三击 0x0C0602 → HOME、长按 0x0C0603 → APP_SWITCH。`framework` 放到 `system/usr/keylayout/` |
| `overlay/src_tether/` | RRO：`com.android.networkstack.tethering` 的 `config_tether_ncm_regexs = [usb\d, ncm\d]` |
| `overlay/src_eth/` | RRO：`com.android.connectivity.resources` 的 `config_ethernet_iface_regex = eth\d+`（否则 EthernetTracker 抢走 usb0） |

overlay 用 apktool 构建，用你自己的密钥签名（v1 签名即可，`targetSdk 29`，priority 200），
输出到 `work/overlay/TB710FUTetheringOverlay.apk` 和 `work/overlay/TB710FUConnectivityOverlay.apk`，`tb710fu.py framework` 会放进 product（没有时会提示，USB 网络共享在 Mac 上可能用不了）。

刷入后开启 USB 网络共享（Mac 需要 NCM）：
```
adb shell settings put global tether_force_usb_functions 1
```
然后在设置里打开"USB 网络共享"。
