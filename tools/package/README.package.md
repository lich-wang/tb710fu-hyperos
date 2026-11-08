# TB710FU HyperOS 4 9008 刷机包

由 [tb710fu-hyperos](https://github.com/lich-wang/tb710fu-hyperos) 的 `tools/build_package.py` 在本机用你自己下载的
移植包、495 免解锁补充包和小米平板 8（yupei）OTA 生成。**不要再分发**：里面有小米、联想、高通和移植作者的镜像。

用同一仓库的 `tools/flash_package.py` 刷：

```bash
python3 tools/flash_package.py backup        # 9008 下完整备份（第一次必须做）
python3 tools/flash_package.py full          # 第 1 阶段：整机线刷 + 清数据，super 不带"查找设备"
# 开机向导里不要登录小米账号；完成后再进 9008：
python3 tools/flash_package.py super-find    # 第 2 阶段：换成带"查找设备"的 super，数据保留
```

| 内容 | 说明 |
|---|---|
| `images/rawprogram0-5.xml`、`patch0-5.xml` | 移植包原样，只把 A 槽 boot/init_boot/dtbo/vendor_boot/vbmeta 指向 `*_v3.img` |
| `images/*.img`（固件） | 移植包 + 495 补充包覆盖后的组合，与参考设备逐字节一致，A/B 两槽 |
| `images/*_v3.img` | v3 启动链（作者原版内核 6.1.128 + testkey 重签 vbmeta），只写 A 槽 |
| `images/super.img` | HyperOS 4，不带"查找设备"（开机向导用） |
| `super_find.img` | HyperOS 4，带"查找设备"（向导完成后写入） |
| `firehose/` | 9008 programmer |
| `zero_64M.bin` | 清 metadata 和 userdata 头部 |
| `manifest.json` | 每个文件的 sha256，刷前自动核对 |

唯一数据分区（persist、modemst1/2、fsg、fsc、devinfo、frp、keystore、ssd、secdata、uefivarstore、cdt、ddr）
以及 proinfo、lenovolock 不在刷写清单里。**永远不要打开小米"查找设备"。**
