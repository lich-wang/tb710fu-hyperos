# TB710FU 免解锁刷小米澎湃 OS 4

在 **不解锁 Bootloader** 的联想小新 Pad Pro GT（TB710FU，骁龙 8 Gen 3 / SM8650）上，
用官方来源构建并刷入 **小米澎湃 OS 4（HyperOS 4，Android 17，框架来自小米平板 8 yupei）**。

> 本项目的逆向分析、代码和文档主要由 AI 编程助手在作者的指导下完成；
> 作者负责设备操作、方案决策和每一步的实机验证。

> ⚠️ **风险提示**：本项目涉及 9008（EDL）线刷和启动镜像替换，操作失误可能导致设备无法开机、
> 数据丢失或失去保修。设备唯一数据分区（`persist`、`modemst1/2`、`fsg`、`fsc`、`devinfo`、`frp`、
> `keystore`、`ssd`、`secdata`、`uefivarstore`、`cdt`、`ddr`）**任何时候都不要写**，动手前先完整备份。
> 一切后果自负。

## 状态：测试中，邀请真机测试

`tools/tb710fu.py` 的每一步都能在电脑上完整跑通（构建已在真实输入上跑过），刷入命令在模拟环境里测过，
**但还没有在任何一台真机上写入过**。愿意帮忙测试的，请看 **[docs/testing.md](docs/testing.md)**，
结果用 [刷机报告](https://github.com/lich-wang/tb710fu-hyperos/issues/new?template=flash-report.yml) 提交（成功、失败、只跑到 `check` 都有用）。

> **推荐用 AI Agent 来做**：让一个能执行命令的 AI 编程 Agent 先读 [`AGENT.md`](AGENT.md)。
> 它只用仓库里的脚本，遇到问题不刷机，整理好报告给你看过后用 `gh` 提 issue；进 9008、写入前的确认都由你来做。

已知限制（和小米平板 8 的硬件差异决定，无法解决）：
- 小米"查找设备"、超级小爱专家模式依赖小米专有 TEE/安全芯片。开机向导里**不要登录小米账号、不要打开查找设备**。
- 小米"手写笔"设置页只认小米笔；联想笔的按键映射已加进系统。

## 原理一览

```
ABL（未解锁，信任 AOSP testkey）
 ├─ boot / init_boot / dtbo / vendor_boot = 联想原厂组件（vendor_boot 的 fstab 补 EROFS 条目），按设备的回滚索引用 testkey 重签
 ├─ vbmeta / vbmeta_system = testkey 重新签名，链到各分区的 verity
 └─ super = 小米 yupei 官方 system / system_ext / product  +  联想原厂 vendor / odm / *_dlkm
```

- **为什么锁 BL 也能刷**：这台机器的 ABL 信任 AOSP 公开测试密钥（`testkey_rsa4096`）。`check` 会确认你的平板是不是这样。
  详见 [docs/boot-chain.md](docs/boot-chain.md)。
- **联想 vendor 跑小米框架需要补什么**：兼容性检查、差异清单、小米 HAL，见 [docs/porting.md](docs/porting.md)。
- **全用官方来源**：联想组件从你自己的平板备份读（或联想官方 9008 包），小米组件来自小米官方 OTA；
  刷机包在你自己的电脑上构建，只属于你这台平板（绑定芯片序列号、当前槽、回滚索引）。仓库不分发任何镜像。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/testing.md](docs/testing.md) | **真机测试指南**：准备、环境检查、构建、刷入、报告 |
| [AGENT.md](AGENT.md) | 给 AI Agent 的规程：只用仓库脚本、遇到问题不刷机、用 `gh` 提 issue |
| [docs/boot-chain.md](docs/boot-chain.md) | 锁 BL 启动原理、AVB/防回滚、备份、启动链、刷机包、刷入与写回、9008、900E |
| [docs/porting.md](docs/porting.md) | 联想原厂 vendor 跑小米框架：兼容性检查、差异清单、小米 HAL、lwky |
| [hyperos/README.md](hyperos/README.md) | 笔键映射、USB 网络共享（NCM）overlay |

## 仓库结构

```
tools/          tb710fu.py + tb/：doctor 环境检查、check 是否信任测试密钥、backup 9008 完整备份、pull 取联想组件、
                import 导入小米 OTA、vendor / framework 构建、bootchain 启动链与签名、package 刷机包、
                flash / restore 9008 写入与写回、report 刷机报告信息
                make_super.py、erofs_meta.py（构建用到）、lp_unpack.py、research/（研究脚本）、ci/、mac/displaymode.swift
hyperos/        笔按键映射、NCM overlay 源码
extras/         Mac 无线副屏色彩测量与描述文件
docs/           文档
AGENT.md        给 AI Agent 的规程
work/           （不入库）你自己设备的备份、底包、构建产物
```

## 相关项目

- 想在这台平板上跑 Linux / Steam：见 [ArmadaOS Freedom](https://github.com/ArmadaOS-Freedom/armadaos-freedom)。
- 本仓库前身 [tb710fu-hyperos-steamos](https://github.com/lich-wang/tb710fu-hyperos-steamos)（HyperOS 4 + SteamOS 双系统，已归档）。

## 许可

GPL-2.0，见 [LICENSE](LICENSE)。本仓库不含任何小米、联想或高通的镜像与固件。
