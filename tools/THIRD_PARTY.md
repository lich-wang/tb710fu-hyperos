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

不放进仓库的：bkerler/edl（`doctor` 给出安装命令，装在 `tools/venv/`）、9008 加载器（联想官方包里的二进制，放 `work/firehose/`）。
