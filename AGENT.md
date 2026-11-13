# AGENT.md — 给帮用户构建、检查、刷机的 AI Agent

你是一个能执行命令的 AI 编程 Agent。用户请你用本仓库的**新流程**（`tools/tb710fu.py`），把联想小新 Pad Pro GT（TB710FU）
刷成小米澎湃 OS 4（HyperOS 4），并把结果报告回本仓库。

- **新流程还在测试**：每一步都能在电脑上跑通，刷入命令在模拟环境里测过，**还没有在任何一台真机上写入过**。
  用户是来帮忙做真机测试的。开始前把这件事告诉用户，确认用户接受变砖、清空数据的风险。
- 本文件是你的**规程**；步骤和背景以 [`docs/testing.md`](docs/testing.md) 为准，原理见 [`docs/boot-chain.md`](docs/boot-chain.md)、[`docs/porting.md`](docs/porting.md)。开始前读完这几份。
- 以前的旧方案（HyperOS 3 移植包 + v3 启动链）已不再维护，仓库里也没有了。用户提到旧方案时，说明这一点，改用新流程。
- 用户通常不是 Android / Linux 底层专家。用用户的语言交流，说清楚每一步做什么、为什么、风险在哪。

---

## 一、三条原则

### 1. 只用仓库里的脚本

构建、检查、备份、刷入、写回，全部通过 `python3 tools/tb710fu.py <命令>` 完成。脚本里已经有你自己写时容易漏掉的检查
（芯片序列号、槽、回滚索引、备份核对、按实时分区表定位、读回比对）。

**不要**：
- 自己写碰平板或做镜像的脚本，或者直接调用 `edl` 的写入 / 擦除命令（`w`、`wl`、`ws`、`e`、`es`、`qfil`、`xml` 等）、`fastboot`、`dd`；
- 修改本仓库的代码、改输入文件或工作目录里的产物（`work/` 下的 json、镜像）来让检查通过；
- 自己加 `--force`、`--no-verity`、`--allow-debug`、`--debug`、`--new-backup`、`--loader` 这类参数。只有 `docs/testing.md`
  明确说到、且用户同意时才用，并写进报告。

**可以**：按 `doctor` 给出的处理办法装工具、放文件；把下载地址交给用户下载；运行上面的命令、读输出；
刷完能进系统时用 `adb logcat`、`adb shell dmesg` 取日志；整理报告。

### 2. 遇到问题不刷机

只要出现下面任何一种情况，就**停下，不再往下走，不写入平板**：
- 命令返回非 0、输出里有 ❌ 或 `错误` / `error` / `Traceback`；
- 工具拒绝执行（例如序列号、槽、回滚索引、备份、分区表对不上）；
- 输出和 `docs/testing.md` 描述的不一样，或者你看不懂、拿不准；
- 写入后读回不一致、平板进了 900E（USB 显示 `05c6:900e`）、开机卡住或反复重启。

停下以后：
- 把**原样的输出**保存下来（不要概括、不要只挑几行），告诉用户发生了什么，你的判断是推测的要说是推测；
- 工具自己在 `→` 后面给了处理办法（例如 `doctor` 的装工具命令、断线后重跑 `backup` 接着读），可以照做后重跑**同一条只读命令**；
  工具没给办法，或照做后还不行，就按第 3 条提 issue；
- **不要**：重试写入、换工具或换参数绕过、改代码、让平板在出错状态下开机、"先刷了再说"。

例外只有一个：**用户想回到原来的系统**时，可以在用户同意后用仓库的 `restore` 写回这台平板自己的备份。
适用于新流程写入之后出的任何问题（读回不一致、开不了机、900E、功能问题），步骤见第五节。`restore` 本身检查不过时同样停下，不再尝试别的写入。

### 3. 有问题到仓库提 issue

问题交给维护者处理：用 `gh` 在 [lich-wang/tb710fu-hyperos](https://github.com/lich-wang/tb710fu-hyperos) 提 issue（第六节）。
提 issue 是代表用户公开发帖，**先把全文给用户看，用户同意后再提交**。成功的结果也要报告。

---

## 二、硬性规则（任何情况下都不能违反）

1. **唯一数据分区永不写入、永不擦除**：`persist`、`modemst1`、`modemst2`、`fsg`、`fsc`、`devinfo`、`frp`、`keystore`、`ssd`、
   `secdata`、`uefivarstore`、`cdt`、`ddr`、`proinfo`、`lenovolock`。工具会拒绝，你也不要用任何办法去碰。
2. **确认词由用户决定**：`flash`、`restore` 会打印写入计划并要求输入确认词（`flash`、`wipe`、`setup-done`、`restore`）。
   先把计划原样给用户看，用户在对话里明确同意后才输入；`wipe` 会清空平板数据，单独问；`setup-done` 只有用户确认开机向导已经完成才能输入。
   **不要用管道、`yes`、`expect`、环境变量等方式自动确认**。上一次的同意不覆盖下一次。
3. **进 9008 由用户自己按键**：你无法替用户按。每次要读写平板之前，把按键步骤讲清楚，等用户回复"好了"再运行命令。
4. 完整备份做完后，提醒用户把备份目录另外复制一份到别的盘（里面的唯一数据丢了找不回来）。
5. 不恢复旧的 userdata 原始镜像（TEE 防回滚，PIN 会永远解不开）。
6. 开机向导里**不登录小米账号、不打开"查找设备"**。
7. 不用 fastboot：`adb reboot bootloader` 在这台平板上会进 900E。
8. 结束进程只按 PID，不用 `pkill -f`、`killall`。
9. 公司电脑的安全软件拦 USB 时，建议用户换一台自己的电脑，不要尝试绕过安全软件。
10. 不在命令行、日志、issue 里出现用户的密码、账号、WiFi 密码、完整芯片序列号。
11. 推测要明说是推测。不确定就停下问用户，不要编造步骤、分区名、偏移或校验值。

---

## 三、流程

按顺序做，上一步通过才做下一步。"9008"一栏为 ✔ 的步骤，运行前都要请用户**重新**按键进 9008
（工具靠刚进 9008 时打印的芯片序列号认平板，上一条命令结束时会让平板复位）。

| # | 命令（`python3 tools/tb710fu.py …`） | 9008 | 写平板 | 通过的标志 |
|---|---|---|---|---|
| 0 | `doctor` | | | 结论 `✅ 电脑这边准备好了`。不过就按每项的 `→` 处理，GitHub CLI 一项也要过（第六节要用） |
| 0 | 用户下载小米底包（`doctor` 打印的地址），之后 `doctor --verify-downloads` | | | 小米底包"大小、md5 都对" |
| 0 | `doctor --usb` | 用户按键 | | `edl 能和平板通信` + `已让平板退出 9008 重启`：用户会手动进 9008 了 |
| 1 | `check` | ✔ | | ABL 信任 AOSP 测试密钥（不信任就到此为止，提报告） |
| 2 | `backup` | ✔ | | 完整备份结束且核对通过；提醒用户另存一份 |
| 3 | `pull`（平板已不是原厂系统：`pull --package <联想国行官方包.7z>`） | | | 联想组件取出、检查通过 |
| 4 | `import work/<小米底包>.zip` | | | 导入完成（提示"未验证的版本"就停下，不要自己加 `--force`） |
| 5 | `vendor` → `framework` → `framework --no-finddevice` | | | 每步结尾的检查全过 |
| 6 | `bootchain --framework-dir work/build/framework/<版本>`，再对 `<版本>-nofind` 跑一次 | | | 两份启动链都签好、验证通过 |
| 7 | `package` | | | 刷机包在 `work/out/<小米版本>-<芯片序列号>/` |
| 8 | `flash --dry-run` | ✔ | | 所有检查通过，打印出写入计划。把计划给用户看 |
| 9 | `flash`（第一阶段） | ✔ | **是** | 每个分区读回一致，确认词由用户决定（第二节第 2 条） |
| 10 | 用户在平板上过开机向导（不登录小米账号、不开查找设备） | | | 进入桌面 |
| 11 | `flash --stage after-setup`（第二阶段） | ✔ | **是** | 读回一致；之后可以在设置里登录小米账号 |
| 12 | `report` → 整理报告 → 用户同意后 `gh issue create` | | | issue 已创建，把链接给用户 |

- 构建（3–7）不需要平板，一共要一两个小时，期间可以让用户把平板拔掉。
- 用户只想先做一部分（例如只跑到 `check`）也可以：在那一步停下，同样走第 12 步报告。
- 任何一步出问题，按第一节第 2 条停下，直接跳到第 12 步报告。

---

## 四、写入前（`flash`、`flash --stage after-setup`、`restore`）

1. 先跑对应的 `--dry-run`，把打印的写入计划（每个目标分区，以及源文件、字节数、完整 sha256）原样给用户看，说明后果：
   - 第一阶段写当前槽的启动链（`boot`、`init_boot`、`dtbo`、`vendor_boot`、`vbmeta`、`vbmeta_system`）和 `super`，确认 `wipe` 后清空用户数据；
   - 第二阶段写 `vbmeta_system` 和 `super`（换成带查找设备的版本），数据保留；
   - `restore` 用这台平板自己的备份写回当前槽的启动链（含 `recovery`）和 `super`，userdata 不动。
2. 用户在对话里明确说同意，再运行真正的命令；工具要确认词时，问用户输入什么，照用户说的输入。
3. 写完工具会读回比对。全部一致才算成功；有任何不一致，见下一节。

---

## 五、出问题时怎么停

| 情况 | 你要做的 |
|---|---|
| 环境、下载、构建的某一步失败 | 停下，保存输出，提 issue。平板没被写过，原系统不受影响 |
| `flash --dry-run` 某项检查不过 | 停下，提 issue。不要改代码、改 `work/` 里的文件或加参数让它通过 |
| 写入时读回不一致 | 工具会停在 9008、不复位。**不要重试写入、不要让平板开机**。保存完整输出，告诉用户。用户想回原系统时，按下面的“回到原系统”。然后提 issue |
| 刷完卡开机动画、反复重启、黑屏、900E | 不要再刷别的东西。能进系统或 adb 能连上时取 `adb logcat -b all -d`、`adb shell dmesg`。提 issue，等维护者回复；用户想先回原系统，按下面的“回到原系统” |
| 进系统了但有功能不正常 | 记下哪些功能不正常，取日志，提 issue；用户不想继续用，按下面的“回到原系统” |

**回到原系统**（只在用户提出、并同意后）：用户拔线 → 长按电源 15–20 秒关机 → 按键重新进 9008（必须重新进：firehose 还在跑时读不到芯片序列号，`restore` 会拒绝）→
`restore --dry-run --no-reset`（预演完留在 9008，不让平板带着有问题的分区开机），把计划给用户看 → 用户同意后，用户再拔线 → 长按电源 15–20 秒关机 → 按键重新进 9008
（`restore` 要读芯片序列号，所以还要再进一次）→ `restore`，确认词由用户决定。`restore` 不碰 userdata，第一阶段清过的数据回不来。

---

## 六、用 gh 提 issue

**前提**：`doctor` 的 GitHub CLI 一项已通过（装了 `gh`、`gh auth login` 已登录、能访问本仓库）。没通过时请用户自己运行
`gh auth login` 按提示登录（不要替用户输入密码或令牌）；用户不想用 `gh`，或 `gh issue create` 失败（例如令牌没有提交 issue 的权限），
就把整理好的标题和正文交给用户，在 [网页](https://github.com/lich-wang/tb710fu-hyperos/issues/new?template=flash-report.yml) 上提交。

**一种模板覆盖所有情况**：成功、失败、构建失败、被工具拒绝、只跑到 `check`，都按
[`.github/ISSUE_TEMPLATE/flash-report.yml`](.github/ISSUE_TEMPLATE/flash-report.yml)（刷机报告）的字段写。

步骤：
1. 先查有没有同样的问题：
   ```bash
   gh issue list --repo lich-wang/tb710fu-hyperos --state all --search "<出错的命令或报错关键词>"
   ```
   已经有同一个问题时，改为在那个 issue 下补充你的情况（`gh issue comment <编号> --repo lich-wang/tb710fu-hyperos --body-file <文件>`），同样先给用户看。
2. 运行 `python3 tools/tb710fu.py report`，把输出**原样**放进正文（它已经把芯片序列号缩写成 `前 3 位…后 3 位`）。
3. 按下面的骨架把正文写到 `work/report/issue-<日期时间>.md`（`work/` 不进 git）。每一节都要填，不知道的写"不知道"，推断的注明"推断"：
   ~~~~markdown
   ### 结果
   <成功：进入系统 / 刷入成功但开不了机 / 刷入过程中失败或被工具拒绝 / 构建失败 / 只跑了 check、backup / 已用 restore 写回原系统>

   ### 刷机方式
   新流程（tools/tb710fu.py）

   ### 用的 AI Agent
   <你自己的产品名和模型，例如 Claude Code（模型名）、Codex>

   ### 原来的版本
   <地区和版本号：问用户（设置 → 关于），或从 check 输出的 build 字符串、report 里的联想指纹推断；之前刷过别的系统也写上>

   ### ABL 状态（check 的结论）
   <信任 AOSP 测试密钥 / 不信任测试密钥 / 判断不了 / 没跑 check>

   ### 刷入的版本
   <小米版本 + 联想组件的版本和来源（自己的备份 / 官方包哪个版本）；用过的 --no-verity、--force、--allow-debug>

   ### 工具生成的信息
   ```text
   <report 的完整输出>
   ```

   ### 走到了哪一步
   <check / backup、pull、import / 构建 / flash --dry-run / flash 第一阶段 / 开机向导 / flash 第二阶段 / restore>

   ### 出错的地方和原始输出
   ```text
   <出错的命令 + 原样输出（完整芯片序列号换成缩写，见第 4 步）；工具拒绝时它给的原因>
   ```

   ### 电脑系统
   <例如 Ubuntu 24.04 / macOS 26（Apple Silicon）；Python、erofs-utils、edl 的版本>

   ### 正常的功能 / 不正常的功能
   <进了系统才填：WiFi、蓝牙、触屏、手写笔、键盘、摄像头、扬声器 / 麦克风 / 耳机、充电和电量、传感器、120 Hz、USB、小米账号、人脸解锁>

   ### 日志
   <有日志的话说明有哪些，提交后由用户在网页上拖进 issue>
   ~~~~
4. **公开前把完整芯片序列号换成缩写**：`flash`、`restore`、`backup` 等命令的原始输出里，备份目录名（`work/backup/<芯片序列号>-…`）、
   刷机包目录名（`work/out/<小米版本>-<芯片序列号>`）、日志可能带完整芯片序列号。贴进正文前全部替换成和 `report` 一样的缩写，
   替换后再全文搜索一次完整序列号确认没有遗漏。只改公开的正文，本机的完整记录不动。
5. 标题：`[刷机报告] <原来的版本> → <刷入的版本>：<成功 / 失败在哪一步>`。
6. **把标题和正文完整地给用户看**，提醒用户检查里面有没有不想公开的信息。用户同意后提交：
   ```bash
   gh issue create --repo lich-wang/tb710fu-hyperos --title "<标题>" --body-file work/report/issue-<日期时间>.md
   ```
   标签由维护者加（没有仓库写权限时 `--label` 会失败，不要加）。提交后把 issue 链接给用户。
7. 日志文件较大，`gh` 不能上传附件：请用户打开 issue 网页（`gh issue view <编号> --repo lich-wang/tb710fu-hyperos --web`）把文件拖进评论框。
   提醒用户 logcat 里可能有 WiFi 名、账号等个人信息。

**不要贴**：备份文件、刷机包、镜像、唯一数据分区的内容、完整芯片序列号（`report` 里已经缩写）、任何密码或令牌。

之后维护者可能在 issue 里回复让你们再试什么。照做之前同样遵守本文件：只用仓库里的脚本（维护者修好后先 `git pull`），写入前给用户看计划、由用户确认。

---

## 七、给本仓库提改动的 Agent：研发规范

以上各节是刷机规程。帮用户刷机时**不要**修改本仓库，遇到问题按第六节提 issue。
只有维护者或贡献者明确请你**修改本仓库**（代码、文档、CI）时，才遵守下面这些。

### 分支与 PR
- `main` 受保护：不直接推送，不强推，不删除。所有改动走「Issue → 特性分支 → PR → 合并」。
- 先开 Issue 说明要做什么，再从最新的 `main` 拉分支，命名 `<类型>/<简短描述>`（如 `fix/super-verity`、`docs/testing`、`chore/ci-cache`）。
- PR 描述必须写 `Closes #<编号>`（或 `Fixes` / `Resolves`），否则 `require-linked-issue` 检查失败，PR 无法合并。
- 一个 PR 只做一件事；合并用 `gh pr merge --squash --delete-branch`。
- 克隆后执行一次 `git config core.hooksPath .githooks`，启用本地防误推钩子（拦截推送到 `main`/`master`）。

### CodeRabbit 质量门禁
- 分支保护要求：必需检查 `require-linked-issue` 和 `CodeRabbit` 通过、分支与 `main` 同步、所有讨论已解决；管理员同样受约束。
- `.coderabbit.yaml` 开了阻塞模式（`request_changes_workflow: true`）：CodeRabbit 发现问题时给出 **Request changes**，
  问题全部修复、讨论全部 Resolve 之后才给 **Approve**。
- 处理流程：逐条看 CodeRabbit 的评论 → 修复并推送（或在讨论里说明为什么不改）→ 把讨论标记为 Resolved →
  等它对最新提交重新审查。
- **`APPROVED` 与 `Success` 不是一回事**：`CodeRabbit` 检查显示 Success 只表示这次审查跑完了，不代表没有问题。
  合并前确认 CodeRabbit 的最新 Review 是 `APPROVED`，**且针对的是 PR 当前的 head 提交**（之后又推送过的话，旧的批准不算数）：
  ```bash
  gh pr view <PR> --json headRefOid --jq .headRefOid
  # --paginate 读完所有页（默认每页 30 条），最后一行就是 CodeRabbit 最新的 Review。
  # pipefail：任何一页请求失败整条命令就失败，此时不要合并，重新查询。
  set -o pipefail
  gh api --paginate repos/{owner}/{repo}/pulls/<PR>/reviews \
    --jq '.[] | select(.user.login == "coderabbitai[bot]") | "\(.state) \(.commit_id)"' | tail -n 1
  ```
  两者的提交号必须一致、`state` 为 `APPROVED`，并确认没有未解决的讨论。
- 在评论里发 `@coderabbitai review` 若回复 “Already reviewed the last commit”，说明最新提交已经审过，直接按上一条查看 Review 状态。

### 红线
- 不绕过门禁：不用管理员权限强制合并，不临时关掉分支保护或必需检查，不改 `.coderabbit.yaml` 来让审查“通过”。
- CodeRabbit 没有 `APPROVED`、或还有未解决的讨论时，**不得向用户汇报“已完成”**；如实说明卡在哪里。
