# 仓库自带的第三方工具

构建刷机包要用 AOSP 的 avbtool / 测试密钥、mkbootimg 和应用测试密钥。为了让克隆下来的仓库直接能用、所有人用同一份，
这里原样放了一份（没有改动），版本固定在下面的提交。代码只在仓库里找它们。

| 目录 | 来源 | 提交 | 许可证 |
|---|---|---|---|
| `avb/` | [platform/external/avb](https://android.googlesource.com/platform/external/avb/+/761178607206f4cb2af79ed9eec52d8cbd814adb)：`avbtool.py`、`test/data/testkey_rsa4096.pem`、`test/data/testkey_rsa2048.pem`、`LICENSE` | `761178607206f4cb2af79ed9eec52d8cbd814adb` | MIT（`avb/LICENSE`） |
| `mkbootimg/` | [platform/system/tools/mkbootimg](https://android.googlesource.com/platform/system/tools/mkbootimg/+/d2bb0af5ba6d3198a3e99529c97eda1be0b5a093)：`mkbootimg.py`、`unpack_bootimg.py`、`repack_bootimg.py`、`gki/generate_gki_certificate.py` | `d2bb0af5ba6d3198a3e99529c97eda1be0b5a093` | Apache-2.0（`mkbootimg/LICENSE`） |
| `apksign/` | [platform/build](https://android.googlesource.com/platform/build/+/045a3d6a3e359633a14853a5a5e1e4f2a11cbdae/target/product/security)：`target/product/security/testkey.pk8`、`testkey.x509.pem` | `045a3d6a3e359633a14853a5a5e1e4f2a11cbdae` | Apache-2.0（AOSP 公开测试密钥） |

两把测试密钥是 AOSP 公开的测试密钥，任何人都能拿到，不是秘密；平板的 ABL 信任它们（`check` 会确认），
所以本项目用它们重签启动链。`doctor` 会核对它们的公钥 sha1。
`apksign/` 是 AOSP 公开的应用测试密钥（`testkey`，证书 SHA-256 `A4:0D:A8:0A:59:D1:70:CA:…`），只用来给打过补丁的小米相机重新签名（见 docs/porting.md 的 #48）。

sha256：

| 文件 | sha256 |
|---|---|
| `avb/avbtool.py` | `e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b` |
| `avb/testkey_rsa4096.pem` | `6a224754880a57ab9cbd308267cd157d94cf05a1c8cb851aec4090e045d24121` |
| `avb/testkey_rsa2048.pem` | `f1d5765a2bdfb92fb08aee021107c7ac1a7a3f590dafd853771c85375ef0fbd7` |
| `avb/LICENSE` | `7b64210dc457e50fdcdf49c44d3efa19d78d6f49aa141862592ac31b700c19d2` |
| `mkbootimg/mkbootimg.py` | `37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c` |
| `mkbootimg/unpack_bootimg.py` | `a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308` |
| `mkbootimg/repack_bootimg.py` | `b5e30899211f4d6ac7b1d6b53056585802aaab9f9b37f81da0f51ddb35291699` |
| `mkbootimg/gki/generate_gki_certificate.py` | `1bb1feec68a13da18d581aa2c631798f86f6bc10b55d587b2dd31446a0f8a203` |
| `apksign/testkey.pk8` | `495675d32e89a149d5abe191f4e9c0e218b9068714e9b53a7c91e164a0741a23` |
| `apksign/testkey.x509.pem` | `a4384ba815b9499a5ce349b4e33c1755278873fe2eac150a068823f526e6dbde` |

更新：从上面的地址按新的提交取同名文件替换，更新本表，再完整跑一遍构建（`tools/ci/build.sh`）核对结果。

## 联想 CN 17.0.04.279 的 abl

| 文件 | 来源 | 字节 | sha256 |
|---|---|---|---|
| `firmware/lenovo-cn-279/abl.elf` | 联想官方 TB710FU CN ZUI 17.0.04.279 9008 线刷包（`TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.279_ST_250808.7z`）里的 `abl.elf`，原样 | 241464 | `45106e3b7864be760a2631d9a12780604040153ac23541e11e2e23e028dd0489` |

联想的二进制（联想证书签名），不是本项目的代码，不在 GPL-2.0 范围内。只给联想官方原厂 ZUXOS 1.5.04.495 的平板用：
那台平板的 abl 只认联想自己的密钥，测试密钥签的启动链要换成这份只认 AOSP 测试密钥的 abl（见 docs/boot-chain.md）。
放进仓库是为了不让每个人为了 1 MiB 的分区再下载约 9 GB 的线刷包；补 0 到 1 MiB 后（`6cfa16e6…`）就是
联想 "495 免解锁补充包" 的 abl.img，也和 Freedom 安装器内置的 `abl_aosp_capable.img` 相同。
代码每次使用都按 `tools/tb/abl.py` 的 `PINNED` 核对 sha256；不想用仓库这份，`tb710fu.py abl <联想官方包>` 可以自己取。

不放进仓库的：bkerler/edl（`doctor` 给出安装命令，装在 `tools/venv/`）、9008 加载器（联想官方包里的二进制，放 `work/firehose/`）。
