# TB710FU 免解锁刷小米澎湃 OS 4

在 **不解锁 Bootloader** 的联想小新 Pad Pro GT（TB710FU，骁龙 8 Gen 3 / SM8650）上，
从联想原装 ZUI 1.5.04.495 刷到 **小米澎湃 OS 4（HyperOS 4，Android 17，移植自小米平板 8）**。

> 本项目的逆向分析、代码和文档主要由 AI 编程助手在作者的指导下完成；
> 作者负责设备操作、方案决策和每一步的实机验证。

> ⚠️ **风险提示**：本项目涉及 9008（EDL）线刷和启动镜像替换，操作失误可能导致设备无法开机、
> 数据丢失或失去保修。设备唯一数据分区（`persist`、`modemst1/2`、`fsg`、`fsc`、`devinfo`、`frp`、
> `keystore`、`ssd`、`secdata`、`uefivarstore`、`cdt`、`ddr`）**任何时候都不要写**，动手前先完整备份。
> 一切后果自负。

## 效果

日常可用：显示 3200×2000@120Hz、WiFi/蓝牙、三个摄像头、传感器、四扬声器、小米账号、人脸解锁（非 TEE）、
Magisk root、USB 网络共享（NCM，Mac 可用）、无线副屏 / 小米互联投屏、手写笔（笔键映射）。

已知限制：
- 小米"查找设备"、超级小爱专家模式依赖小米专有 TEE/安全芯片，无法使用。**不要打开查找设备**。
- 小米"手写笔"设置页显示未连接（只认小米笔），笔本身的书写、压感、按键都正常。

## 原理一览

```
ABL（未解锁，信任 AOSP testkey）
 ├─ boot / init_boot / dtbo / vendor_boot = 移植作者原版（GKI 6.1.128，fstab 支持 EROFS），testkey 重签
 ├─ vbmeta = testkey_rsa4096 重新签名；vbmeta_system / pvmfw / recovery 保留 ZUI 495 版（防回滚）
 └─ super = HyperOS 4（yupei）system / system_ext / product  +  HyperOS 3 移植包的 vendor / odm / *_dlkm
```

- **为什么锁 BL 也能刷**：这台机器的 ABL 信任 AOSP 公开测试密钥（`testkey_rsa4096`），
  社区"免解锁补充包"正是利用这一点。详见 [docs/boot-chain.md](docs/boot-chain.md)。
- **HyperOS 4 怎么来的**：vendor 侧沿用 HyperOS 3 移植包（Android 14 vendor 接口），只把框架侧换成小米平板 8 的
  HyperOS 4，并补上 SELinux、属性、overlay 等兼容改动。详见 [docs/hyperos4.md](docs/hyperos4.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/flash-guide.md](docs/flash-guide.md) | **刷机指南**：从原装 ZUI 到 HyperOS 4 的完整步骤 |
| [docs/boot-chain.md](docs/boot-chain.md) | 锁 BL 启动原理、AVB/防回滚、EDL 备份与线刷、救砖（900E）、v3 启动链 |
| [docs/hyperos4.md](docs/hyperos4.md) | HyperOS 4 移植：底包选择、改动清单、构建与刷入、排错记录 |
| [docs/porting.md](docs/porting.md) | 新流程：联想原厂 vendor 跑小米框架需要补什么（兼容性检查、差异清单、小米 HAL、lwky） |
| [hyperos/README.md](hyperos/README.md) | 笔键映射、SELinux 兼容策略、RRO overlay、Magisk 模块 |

## 仓库结构

```
tools/          tb710fu.py + tb/（新流程入口，见 #1；目前有 `check` 只读检查是否信任 AOSP 测试密钥、`backup` 9008 完整备份、`pull` 取出联想原厂组件、`import` 导入小米平板 8 官方包、`vendor` 构建 vendor 侧、`framework` 构建框架侧、`bootchain` 启动链与签名）、
                make_v3_bootchain.py（v3 启动链）、build_os4.py（HyperOS 4 分区构建）、make_super.py、lp_unpack.py、
                erofs_meta.py、sepolicy_fix.py、flash_super.sh、mac/displaymode.swift
hyperos/        笔按键映射、SELinux 兼容策略、RRO overlay 源码、Magisk 模块
extras/         Mac 无线副屏色彩测量与描述文件
docs/           文档
AGENT.md        给 AI Agent 的刷机操作规程
work/           （不入库）你自己设备的备份、底包、构建产物
```

## 怎么刷

完整步骤：**[docs/flash-guide.md](docs/flash-guide.md)**——从联想官方 ZUI 1.5.04.495 开始，进 9008 备份，
线刷 HyperOS 3 移植包，换 v3 启动链，构建并刷入 HyperOS 4。

> **推荐用 AI Agent 来刷**：让一个能执行命令的 AI 编程 Agent 读取 [`AGENT.md`](AGENT.md) 和刷机指南，
> 由它执行命令、校验结果，需要你动手时它会停下来告诉你。

## 相关项目

- 想在这台平板上跑 Linux / Steam：见 [ArmadaOS Freedom](https://github.com/mydanyi/armadaos-freedom)（TB710FU 支持正在合入上游，可与 HyperOS 共存）。
- 本仓库前身 [tb710fu-hyperos-steamos](https://github.com/lich-wang/tb710fu-hyperos-steamos)（HyperOS 4 + SteamOS 双系统，已归档）。

## 许可

GPL-2.0，见 [LICENSE](LICENSE)。本仓库不含任何小米、联想或高通的镜像与固件。
