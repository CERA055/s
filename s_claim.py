#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
casino.soliumbra.com 自动领取（daily / hourly / relief / copper）

== 关于 Cloudflare 的准确情况 ==

站点是「服务端按需索要 Turnstile」的 step-up 模式，不是首页挑战页，所以静态抓 HTML
看不到任何 challenge 标记。真实机制（bundle 偏移 883396 处）：

    catch (z) {
      if (kind === "hourly" && z.code === "hourly_turnstile_required") {
        const X = z.details;               // ← 服务端下发，前端无硬编码
        // X = { siteKey, cData, action: "turnstile-hourly" }
        b({ challenge: { action: X.action, siteKey: X.siteKey, cData: X.cData } });
      }
    }
    // 验证通过后：V("hourly", el, token) → POST 带 {"cf-turnstile-response": token} 重发

结论：
  * daily 目前没有 turnstile 分支（只有 hourly 有）
  * hourly 是否索要，由服务端按 session / IP 声誉 / 频率决定，不是恒定必验
  * siteKey、cData、action 全部由服务端明文下发 → 可以抓出来交给打码平台
  * token 与 cData 绑定，且打码平台返回的 UA 必须原样用于回填请求

用法：
    python s_claim.py --check
    python s_claim.py --kind daily
    python s_claim.py --kind hourly
    python s_claim.py --kind hourly --provider capsolver
    python s_claim.py --kind hourly --turnstile "<手动拿到的 token>"
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    sys.exit("先装依赖: pip install requests")

BASE = "https://casino.soliumbra.com"
API = BASE + "/api"
PAGE_URL = BASE + "/"

UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookie.txt")
KEY_FILE = os.path.join(HERE, "solver_keys.json")
LOG_FILE = os.path.join(HERE, "claim.log")

ENDPOINTS = {
    "daily":  "/wallet/claim/daily",
    "hourly": "/wallet/claim/hourly",
    "relief": "/wallet/relief",
    "copper": "/wallet/copper-relief",
}
LABELS = {"daily": "每日签到", "hourly": "整点补给",
          "relief": "救济金", "copper": "游乐铜板"}


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- session

def build_session():
    # 优先级：环境变量（GitHub Actions / 云函数）> cookie.txt（本地 / Termux）
    raw = os.environ.get("S_COOKIE", "").strip()
    if not raw:
        if not os.path.exists(COOKIE_FILE):
            sys.exit("没有 cookie：设 S_COOKIE 环境变量，或写 %s" % COOKIE_FILE)
        # utf-8-sig：Windows 记事本 / Set-Content 会写 BOM，
        # BOM 混进 cookie 值会让 urllib3 抛 latin-1 编码错误
        raw = open(COOKIE_FILE, encoding="utf-8-sig").read().strip()
    raw = raw.lstrip("\ufeff")
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": BASE,
        "Referer": PAGE_URL,
    })
    for part in raw.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        s.cookies.set(k.strip(), v.strip(), domain="casino.soliumbra.com", path="/")
    return s


def get_wallet(s):
    r = s.get(API + "/wallet", timeout=20)
    if r.status_code == 401:
        return None, "401 登录态失效，需要重新用 Discord 登一次然后导出新 cookie"
    if not r.ok:
        return None, "GET /wallet HTTP %s: %s" % (r.status_code, r.text[:200])
    try:
        j = r.json()
    except ValueError:
        return None, "响应不是 JSON: %s" % r.text[:200]
    # 真实结构：{"wallet": {...}, "ledger": [...]}
    return (j.get("wallet") or j), None


# ---------------------------------------------------------------- turnstile

class Solver:
    """Turnstile 打码。siteKey / cData / action 来自服务端下发的 details。"""

    def __init__(self, provider, key):
        self.provider = provider
        self.key = key

    def solve(self, sitekey, action=None, cdata=None):
        if self.provider == "capsolver":
            return self._capsolver(sitekey, action, cdata)
        if self.provider == "yescaptcha":
            return self._yescaptcha(sitekey, action, cdata)
        raise SystemExit("未知 provider: %s" % self.provider)

    def _task(self, sitekey, action, cdata, type_name):
        task = {"type": type_name, "websiteURL": PAGE_URL, "websiteKey": sitekey}
        if action:
            task["action"] = action
        if cdata:
            task["cdata"] = cdata
        return task

    def _poll(self, endpoint, task_id):
        for _ in range(40):
            time.sleep(3)
            try:
                r = requests.post(endpoint, json={"clientKey": self.key,
                                                  "taskId": task_id}, timeout=30)
                j = r.json()
            except Exception as e:
                log("轮询异常: %s" % e)
                continue
            if j.get("status") == "ready":
                sol = j.get("solution") or {}
                # Turnstile 的 token 常与 UA 绑定，打码平台会回传它算 token 用的 UA
                return sol.get("token") or sol.get("gRecaptchaResponse"), sol.get("userAgent")
            if j.get("errorId") not in (None, 0):
                log("打码失败: %s" % json.dumps(j, ensure_ascii=False)[:300])
                return None, None
        log("打码超时")
        return None, None

    def _capsolver(self, sitekey, action, cdata):
        create = "https://api.capsolver.com/createTask"
        payload = {"clientKey": self.key,
                   "task": self._task(sitekey, action, cdata, "TurnstileTaskProxyLess")}
        j = requests.post(create, json=payload, timeout=30).json()
        if j.get("errorId"):
            log("CapSolver createTask 失败: %s" % json.dumps(j, ensure_ascii=False)[:300])
            return None, None
        log("CapSolver 已建单 %s，等 token…" % j.get("taskId"))
        return self._poll("https://api.capsolver.com/getTaskResult", j["taskId"])

    def _yescaptcha(self, sitekey, action, cdata):
        create = "https://api.yescaptcha.com/createTask"
        payload = {"clientKey": self.key,
                   "task": self._task(sitekey, action, cdata, "TurnstileTaskProxyless")}
        j = requests.post(create, json=payload, timeout=30).json()
        if j.get("errorId"):
            log("YesCaptcha createTask 失败: %s" % json.dumps(j, ensure_ascii=False)[:300])
            return None, None
        return self._poll("https://api.yescaptcha.com/getTaskResult", j["taskId"])


def load_solver_keys():
    keys = {}
    if os.path.exists(KEY_FILE):
        try:
            keys.update(json.load(open(KEY_FILE, encoding="utf-8-sig")))
        except (OSError, ValueError):
            pass
    # 环境变量覆盖，方便 CI / 云函数
    if os.environ.get("CAPSOLVER_KEY"):
        keys["capsolver"] = os.environ["CAPSOLVER_KEY"]
    if os.environ.get("YESCAPTCHA_KEY"):
        keys["yescaptcha"] = os.environ["YESCAPTCHA_KEY"]
    return keys


def notify(text):
    """Telegram 推送。没配 token 就静默跳过。"""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    try:
        requests.post("https://api.telegram.org/bot%s/sendMessage" % tok,
                      json={"chat_id": chat, "text": text,
                            "disable_web_page_preview": True},
                      timeout=15)
    except Exception as e:
        print("[notify] 推送失败: %s" % e)


# ---------------------------------------------------------------- claim

def post_claim(s, kind, token=None, ua=None):
    """发一次领取。token 为 None 时按前端逻辑发空 body。"""
    url = API + ENDPOINTS[kind]
    body = None if token is None else {"cf-turnstile-response": token}
    hdrs = {}
    if ua:                       # token 与 UA 绑定时必须换成解 token 时的 UA
        hdrs["User-Agent"] = ua
    r = s.post(url, json=body, headers=hdrs, timeout=25)
    try:
        data = r.json() if r.text.strip() else {}
    except ValueError:
        data = {"raw": r.text[:300]}
    return r.status_code, data


AVAIL_KEY = {"daily": "dailyAvailable", "hourly": "hourlyAvailable",
             "relief": "reliefAvailable", "copper": "copperReliefAvailable"}
NEXT_KEY = {"daily": "nextDailyAt", "hourly": "nextHourlyAt",
            "relief": "nextReliefAt", "copper": "nextCopperReliefAt"}


def claim(s, kind, solver=None, manual_token=None):
    """返回 (ok: bool, note: str)。ok=True 包含「还没到点」这种正常态。"""
    # 先看能不能领：不可领就别发请求，也让 CI 显示成成功而不是失败
    w, werr = get_wallet(s)
    if w is None:
        log("%s 取钱包失败：%s" % (LABELS[kind], werr))
        return False, "auth"
    if not w.get(AVAIL_KEY[kind], True):
        log("%s 现在不可领（下次 %s），跳过"
            % (LABELS[kind], _ts(w.get(NEXT_KEY[kind]))))
        return True, "not_available"

    code, data = post_claim(s, kind)

    if code == 401:
        log("%s 401 —— 登录态失效" % LABELS[kind])
        return False, "auth"

    ok = 200 <= code < 300
    if ok:
        got = data.get("claimed")
        w = data.get("wallet") or {}
        log("%s 领到 %s | 赠金 %s / 铜板 %s"
            % (LABELS[kind], got, w.get("bonusBalance"), w.get("copperBalance")))
        return True, "claimed"

    err = data.get("error") or data.get("code") or ""
    msg = data.get("message") or ""
    details = data.get("details") or {}

    # 服务端 step-up：只有 hourly 走这条分支
    if str(err).endswith("_turnstile_required") or "turnstile" in str(err):
        log("%s 触发人机验证：code=%s action=%s" % (LABELS[kind], err, details.get("action")))
        sitekey = details.get("siteKey")
        cdata = details.get("cData")
        action = details.get("action")
        if not (sitekey and cdata and action):
            log("details 不完整，无法自动解：%s" % json.dumps(details, ensure_ascii=False)[:300])
            return False, "turnstile_details_missing"

        log("siteKey=%s" % sitekey)
        log("cData=%s" % cdata)
        log("action=%s" % action)

        token, ua = manual_token, None
        if token is None and solver is not None:
            token, ua = solver.solve(sitekey, action=action, cdata=cdata)
        if not token:
            log("没有 token。两条路：--turnstile <token> 手动填，或配好 solver_keys.json 用打码平台")
            return False, "turnstile_needed"

        log("拿到 token %s…，回填重发" % token[:24])
        code2, data2 = post_claim(s, kind, token=token, ua=ua)
        if 200 <= code2 < 300:
            got = data2.get("claimed")
            w = data2.get("wallet") or {}
            log("%s 验证通过并领到 %s | 赠金 %s / 铜板 %s"
                % (LABELS[kind], got, w.get("bonusBalance"), w.get("copperBalance")))
            return True, "claimed_after_turnstile"

        err2 = data2.get("error") or data2.get("code") or ""
        if err2 in ("hourly_not_ready", "daily_not_ready", "not_ready"):
            log("%s 验证通过但未到领取时间（%s）" % (LABELS[kind], err2))
            return True, "not_ready"
        log("%s 回填后仍失败 HTTP %s: %s"
            % (LABELS[kind], code2, json.dumps(data2, ensure_ascii=False)[:300]))
        return False, "after_turnstile_failed"

    # 服务端说「还没到点」也算正常，不要当失败
    if str(err).endswith("_not_ready"):
        log("%s 未到领取时间（%s）" % (LABELS[kind], err))
        return True, "not_ready"

    log("%s 失败 HTTP %s code=%s: %s" % (LABELS[kind], code, err, msg[:200]))
    return False, err or "http_%s" % code


def _ts(ms):
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")
    except (OSError, ValueError):
        return "-"


def show_slots(wallet):
    """wallet 真实字段：xxxAvailable / nextXxxAt / xxxReward"""
    print("  余额：tokens %s / 赠金 %s / 铜板 %s" % (
        wallet.get("balance"), wallet.get("bonusBalance"), wallet.get("copperBalance")))
    slots = (
        ("daily",  "dailyAvailable",  "nextDailyAt",  "dailyReward"),
        ("hourly", "hourlyAvailable", "nextHourlyAt", "hourlyReward"),
        ("relief", "reliefAvailable", "nextReliefAt", "reliefReward"),
        ("copper", "copperReliefAvailable", "nextCopperReliefAt", "copperReliefReward"),
    )
    for kind, avail_f, next_f, reward_f in slots:
        print("  %-6s %-5s 面额 %-8s %s  下次 %s" % (
            LABELS[kind], kind, wallet.get(reward_f),
            "可领" if wallet.get(avail_f) else "不可领",
            _ts(wallet.get(next_f))))
    rb = wallet.get("rewardBonus") or {}
    if rb:
        print("  倍率 x%s （%s 个有效项目）" % (rb.get("multiplier"), rb.get("validProjectCount")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=list(ENDPOINTS))
    ap.add_argument("--all", action="store_true", help="daily + hourly")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--turnstile", help="手动提供已解出的 turnstile token")
    ap.add_argument("--provider", choices=["capsolver", "yescaptcha"],
                    help="打码平台；不填则按 solver_keys.json / 环境变量自动选")
    ap.add_argument("--notify", action="store_true", help="结果推 Telegram")
    ap.add_argument("--quiet-ok", action="store_true", help="成功时不推送")
    args = ap.parse_args()

    s = build_session()
    wallet, err = get_wallet(s)
    if wallet is None:
        log(err)
        if args.notify:
            notify("s 登录态失效，需要重新导出 cookie")
        return 2
    log("登录态 OK")
    show_slots(wallet)
    if args.check:
        return 0

    # 自动挑打码平台：显式参数优先，其次看环境变量 / 密钥文件
    keys = load_solver_keys()
    provider = args.provider
    if not provider:
        provider = "capsolver" if keys.get("capsolver") else (
            "yescaptcha" if keys.get("yescaptcha") else None)
    solver = None
    if provider:
        if not keys.get(provider):
            sys.exit("provider=%s 但拿不到 key" % provider)
        solver = Solver(provider, keys[provider])
        log("打码平台: %s" % provider)
    else:
        log("未配置打码平台 —— 若触发 Turnstile 只能失败退出")

    kinds = [args.kind] if args.kind else (["daily", "hourly"] if args.all else ["daily"])
    worst = 0
    lines = []
    for k in kinds:
        ok, note = claim(s, k, solver=solver, manual_token=args.turnstile)
        lines.append("%s: %s" % (LABELS[k], "OK" if ok else note))
        if not ok:
            worst = 2 if note == "auth" else 1
            if note == "auth":
                break

    summary = "\n".join(lines)
    log("汇总 -> %s" % summary.replace("\n", " | "))
    if args.notify and not (args.quiet_ok and worst == 0):
        notify("s 领取\n" + summary)
    return worst


if __name__ == "__main__":
    sys.exit(main())
