# casino.soliumbra.com 自动领取（后台版）

## 一、Cloudflare 是怎么拦的

**「服务端按需索要 Turnstile」的 step-up 模式，不是首页挑战页。**

静态抓 HTML 什么都看不到 —— `GET /` 返回 1787 字节纯静态 SPA 外壳，没有
`cf-mitigated: challenge`，没有 `/cdn-cgi/challenge-platform/`，`/cdn-cgi/trace` 直出 200。
挑战是**在你点「领取」发请求之后**才出现。

代码（`/assets/index-RJkm-6Ng.js` 偏移 883396）：

```js
if (Z === "hourly" && z instanceof Ze && z.code === "hourly_turnstile_required") {
  const X = z.details;              // ← 服务端下发，前端无硬编码
  if (X?.action === "turnstile-hourly"
      && typeof X.siteKey === "string" && typeof X.cData === "string") {
    b({ challenge: { action: X.action, siteKey: X.siteKey, cData: X.cData } });
```

| 事实 | 含义 |
|---|---|
| `hourly_turnstile_required` | 只有 **hourly** 有这个分支，daily 没有 |
| `siteKey / cData / action` 服务端下发 | 无法预取，必须先打一次请求 |
| `action === "turnstile-hourly"` | 打码时必须带 |
| 回填 `{"cf-turnstile-response": token}` 重发 | 原接口，不是新端点 |
| 按 session / IP 声誉 / 频率判定 | 不是恒定必验 |
| token 常与 UA 绑定 | 打码平台回传的 `userAgent` 必须用于回填 |

## 二、能不能绕过

1. **让它不索要**（零成本先试）：手机真浏览器手领一次，拿到 `cf_clearance` 后导出完整 cookie。
   `cf_clearance` 绑 UA+IP，4G 的 IP 漂移会失效。
2. **自动满足它**（全自动）：details 明文下发 → 提交 CapSolver/YesCaptcha（带 siteKey+action+cdata）
   → 回填 token 并换 UA。脚本已实现。
3. **真浏览器**：Turnstile 非交互模式对真浏览器通常自动过，但 Termux 跑不动 Chromium。

## 三、后台方案怎么选

关键前提：**签到是纯 HTTP，不需要浏览器**。所以「后台」的本质是「有个地方能常年按点发请求」。

| 方案 | 后台可靠性 | 成本 | cookie 在哪 | 适合 |
|---|---|---|---|---|
| **GitHub Actions** | ★★★★★ 不用你开机 | 免费 | GitHub Secrets | 推荐默认 |
| VPS / NAS / 树莓派 | ★★★★★ | 有机器就行 | 自己机器 | 已有设备 |
| 手机 Termux 守护 | ★★★ 会被 Doze 杀 | 免费 | 手机上 | 不想让 cookie 出本机 |
| 手机 Tasker / Auto.js | ★★ 要屏幕 / 被杀 | 免费 | 手机上 | 不推荐 |

**手机后台不可靠的根因**：Android Doze 在锁屏静止后会把进程冻住，`crond` 到点根本不触发，
唤醒后才补跑一次。`crond` 在 Termux 里就废在这儿，所以下面给的是 `while` + `wakelock` 轮询版。

## 四、GitHub Actions（推荐）

### 1. 建仓

把本目录推成一个仓库（private 就行）：

```bash
git init && git add . && git commit -m init
git branch -M main
git remote add origin git@github.com:<你>/soliumbra-claim.git
git push -u origin main
```

`.github/workflows/claim.yml` 已经在正确位置。**别把 `cookie.txt` 提交上去** ——
脚本优先读环境变量，仓库里不需要真文件（建议加 `.gitignore`）。

### 2. 配 Secrets

Repo → Settings → Secrets and variables → Actions → New repository secret：

| Name | 值 |
|---|---|
| `SOLIUMBRA_COOKIE` | 浏览器里复制的完整 Cookie 头（含 `cf_clearance`、`__cf_bm`） |
| `CAPSOLVER_KEY` | CapSolver 的 key（可选，但 hourly 大概率需要） |
| `YESCAPTCHA_KEY` | 备选打码平台（可选） |
| `TELEGRAM_BOT_TOKEN` | 可选，失败时推送到 Telegram |
| `TELEGRAM_CHAT_ID` | 可选 |

### 3. 试跑

Actions → soliumbra-claim → Run workflow。看日志里 `登录态 OK` 和 `汇总 -> ...`。

### 4. 已知限制

- **schedule 会延迟**：GitHub 官方说明高峰期可能延迟几分钟到十几分钟，`cron: "3 * * * *"`
  实际可能在 `:05 ~ :18` 之间跑。整点补给若是「整点后一段时间内可领」就不影响；若卡得很死，
  改成提前触发或干脆换 VPS。
- **private repo 连续 60 天无 commit 会被自动停掉 schedule**。加个每月空的 commit，或者用
  workflow 自己 keep-alive。
- **免费额度**：private repo 每月 2000 分钟，每小时 1 次 × 每次 <1 分钟 ≈ 720 分钟，够用。
  public repo 全免费（但别把 cookie 放 public）。

## 五、手机 Termux（cookie 不出本机）

```bash
pkg install python termux-api
pip install requests
mkdir -p ~/soliumbra && cd ~/soliumbra   # 放 soliumbra_claim.py + cookie.txt
chmod +x run_termux.sh
mkdir -p ~/.termux/boot
cp run_termux.sh ~/.termux/boot/claim.sh && chmod +x ~/.termux/boot/claim.sh
```

三件必须做的，否则一定被杀：

1. 装 **Termux:Boot** 插件并打开一次（开机自启）
2. 系统设置 → 电池 → Termux → **不限制 / 允许后台活动**
3. `run_termux.sh` 里的 `termux-wake-lock`（已自带）

`run_termux.sh` 用 `while true; sleep 20` 轮询，整点后 05 分领补给，零点那轮连 daily 一起做。
纯 `crond` 在 Doze 下不可靠，所以没用它。

## 六、用法

```bash
python soliumbra_claim.py --check                     # 验登录态
python soliumbra_claim.py --kind daily
python soliumbra_claim.py --kind hourly               # 有打码平台会自动用
python soliumbra_claim.py --all --notify --quiet-ok   # daily + hourly，只在失败时推 TG
```

退出码：

| 码 | 含义 |
|---|---|
| 0 | 成功（含 `hourly_not_ready` 这种正常态） |
| 1 | 一般失败 |
| 2 | 登录态失效，重新导出 cookie |

## 七、验证过的

`test_stepup.py` 用本地 mock server 模拟服务端索要 Turnstile：

```
[1] hourly(带 solver) -> ok=True  claimed_after_turnstile
[2] daily(无需验证)   -> ok=True  claimed
[3] hourly(无 solver) -> ok=False turnstile_needed      ← 如实报告，不假装成功
[4] hourly(手动 token)-> ok=True  claimed_after_turnstile
ALL PASS
```

断言了「回填请求的 UA 必须换成解 token 时的 UA」。另外实测过环境变量注入路径能正常打到
真实站点（`SOLIUMBRA_COOKIE` 伪造值 → 真实 401，退出码 2）。

```bash
python test_stepup.py
```
