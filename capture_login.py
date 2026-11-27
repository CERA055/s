#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打开真 Chrome 让你完成 Discord 登录，登录成功后立刻取 cookie 写进 cookie.txt。

为什么这样做：session 和 cf_clearance 都是 HttpOnly，JS 读不到；浏览器 cookie 库
在运行时被独占锁定、新版还是 App-Bound 加密。而 Playwright 的 context.cookies()
能直接拿到 HttpOnly 那一层，绕开全部这些麻烦。

    python capture_login.py                 # 有头窗口，你手动登录
    python capture_login.py --timeout 900
    python capture_login.py --headless      # 只在已有 profile 已登录时用
"""

import argparse
import os
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("先装: pip install playwright")

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, ".browser")
OUT = os.path.join(HERE, "cookie.txt")
BASE = "https://casino.soliumbra.com"
DOMAIN_HINT = "s"


def launch(p, headless):
    """优先用系统 Chrome（更像个真人浏览器，Discord 登录也更顺）。"""
    common = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
        viewport={"width": 1280, "height": 900},
    )
    try:
        return p.chromium.launch_persistent_context(PROFILE, channel="chrome", **common)
    except Exception as e:
        print("[!] 系统 Chrome 起不来（%s），退回 playwright 自带 chromium" % e)
        return p.chromium.launch_persistent_context(PROFILE, **common)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600, help="等登录的秒数")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--poll", type=int, default=2)
    args = ap.parse_args()

    with sync_playwright() as p:
        ctx = launch(p, args.headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        print("浏览器已打开。请点「用 Discord 登录」完成登录。")
        print("等待中（最多 %d 秒）…" % args.timeout)

        deadline = time.time() + args.timeout
        wallet = None
        while time.time() < deadline:
            try:
                r = ctx.request.get(BASE + "/api/wallet", timeout=15000)
                if r.status == 200:
                    wallet = r.json()
                    break
                if r.status == 401:
                    sys.stdout.write(".")
                    sys.stdout.flush()
            except Exception:
                pass
            page.wait_for_timeout(args.poll * 1000)

        if wallet is None:
            print("\n超时，没检测到登录成功。")
            ctx.close()
            return 1

        print("\n登录成功。")
        print("  赠金 %s / 铜板 %s" % (wallet.get("bonusBalance"), wallet.get("copperBalance")))

        cks = [c for c in ctx.cookies() if DOMAIN_HINT in (c.get("domain") or "")]
        names = sorted({c["name"] for c in cks})
        print("  取到 %d 条 cookie: %s" % (len(cks), ", ".join(names)))

        missing = {"cf_clearance", "__cf_bm"} - set(names)
        if missing:
            print("  ! 缺 %s —— 不影响登录，但 hourly 更容易被索要 Turnstile"
                  % ", ".join(sorted(missing)))

        cookie = "; ".join("%s=%s" % (c["name"], c["value"]) for c in cks)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("Cookie: " + cookie + "\n")
        try:
            os.chmod(OUT, 0o600)
        except OSError:
            pass
        print("\n已写入 %s（%d 字节）" % (OUT, len(cookie)))
        ctx.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
