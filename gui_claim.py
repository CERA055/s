#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI 领取：启动有头真实 Chrome，点页面上的「领取」按钮，让站点前端自己处理 Turnstile。

和 solve_once.py 的关键区别：
  solve_once  = 自己调 API + 自己 render Turnstile widget  → 失败（widget 不受信任）
  gui_claim   = 点页面按钮，完全走站点自己的前端逻辑        → 和真人操作等价

    python gui_claim.py                 # 领整点补给
    python gui_claim.py --kind daily
    python gui_claim.py --dry-run       # 只看有没有按钮，不点
"""

import argparse
import json
import os
import re
import sys
import time

try:
    from patchright.sync_api import sync_playwright
    ENGINE = "patchright"
except ImportError:
    try:
        from playwright.sync_api import sync_playwright
        ENGINE = "playwright"
    except ImportError:
        sys.exit("先装: pip install patchright")

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, ".browser")
COOKIE_FILE = os.path.join(HERE, "cookie.txt")
BASE = "https://casino.soliumbra.com"
API = BASE + "/api"

LABELS = {"daily": "每日签到", "hourly": "整点补给",
          "relief": "救济金", "copper": "游乐铜板"}


def load_cookies():
    raw = open(COOKIE_FILE, encoding="utf-8-sig").read().strip().lstrip("\ufeff")
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]
    out = []
    for part in raw.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out.append({"name": k.strip(), "value": v.strip(),
                    "domain": "casino.soliumbra.com", "path": "/"})
    return out


def api_wallet():
    """用 cookie 直接查钱包，用来确认点击到底有没有生效。"""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for c in load_cookies():
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path="/")
    r = s.get(API + "/wallet", timeout=20)
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("wallet") or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="hourly", choices=list(LABELS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=int, default=15, help="点击后等多少秒")
    args = ap.parse_args()

    label = LABELS[args.kind]
    print("[%s] 引擎=%s 目标=%s" % (time.strftime("%H:%M:%S"), ENGINE, label))

    before = api_wallet()
    if before is None:
        print("cookie 失效，先跑 capture_login.py")
        return 2
    print("  赠金 %s / 铜板 %s" % (before.get("bonusBalance"), before.get("copperBalance")))

    avail_key = {"daily": "dailyAvailable", "hourly": "hourlyAvailable",
                 "relief": "reliefAvailable", "copper": "copperReliefAvailable"}[args.kind]
    if not before.get(avail_key, True):
        print("  %s 现在不可领，退出" % label)
        return 0

    common = dict(headless=False, no_viewport=True)
    if ENGINE == "playwright":
        common.update(args=["--disable-blink-features=AutomationControlled"],
                      ignore_default_args=["--enable-automation"])

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, channel="chrome", **common)
        try:
            ctx.add_cookies(load_cookies())
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(BASE + "/wallet", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            btn = page.locator('button[aria-label*="%s"]' % label).filter(
                has_text=re.compile("领取"))
            n = btn.count()
            print("  找到 %d 个「%s 领取」按钮" % (n, label))
            if n == 0:
                # 兜底：任何文字是「领取」的按钮
                btn = page.locator('button:has-text("领取")')
                n = btn.count()
                print("  兜底选择器找到 %d 个" % n)
            if n == 0:
                print("  没有可点的领取按钮")
                page.screenshot(path=os.path.join(HERE, "gui_nobtn.png"))
                return 1

            if args.dry_run:
                print("  dry-run，不点")
                return 0

            target = btn.first
            target.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            target.click(timeout=15000)
            print("  已点击，等 %d 秒（Turnstile 若弹窗会在真实浏览器里自动过）…" % args.wait)

            # patchright 的 profile 太新，CF 会给交互式验证框。
            # CDP 派发的鼠标事件 isTrusted=true，点它有机会过。
            clicked_cb = False
            for _ in range(6):
                page.wait_for_timeout(1500)
                try:
                    frame = page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
                    if frame.locator('body').count() == 0:
                        continue
                    for inner in ('input[type="checkbox"]', '#challenge-stage', 'label'):
                        loc = frame.locator(inner)
                        if loc.count():
                            loc.first.click(timeout=4000, force=True)
                            print("  点了 Turnstile 里的 %s" % inner)
                            clicked_cb = True
                            break
                except Exception:
                    pass
                if clicked_cb:
                    break
            if not clicked_cb:
                print("  没看到 Turnstile 复选框（可能是静默验证）")

            deadline = time.time() + args.wait
            while time.time() < deadline:
                page.wait_for_timeout(1000)
                cur = api_wallet()
                if cur and not cur.get(avail_key, True):
                    print("  成功！赠金 %s / 铜板 %s"
                          % (cur.get("bonusBalance"), cur.get("copperBalance")))
                    page.screenshot(path=os.path.join(HERE, "gui_ok.png"))
                    return 0

            page.screenshot(path=os.path.join(HERE, "gui_timeout.png"))
            print("  %d 秒内没确认到领取成功，截图 gui_timeout.png" % args.wait)
            return 1
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
