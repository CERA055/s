#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用真实浏览器代打一次 Turnstile，把当前这小时的补给领掉。

原理：Turnstile 的 non-interactive 模式对真实浏览器通常自动通过。
页面内先直接调 claim API；若返回 xxx_turnstile_required，就在同源页面里
render 一个 Turnstile widget 拿 token，再带 token 重发。

    python solve_once.py                # 有头，成功率高
    python solve_once.py --headless
    python solve_once.py --kind daily
"""

import argparse
import json
import os
import sys

try:
    # patchright 是 playwright 的隐身补丁分支：去掉 Runtime.enable 泄漏、
    # navigator.webdriver、CDP 指纹等，Turnstile 对它比原生 playwright 宽容
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

ENDPOINTS = {
    "daily": "/wallet/claim/daily",
    "hourly": "/wallet/claim/hourly",
    "relief": "/wallet/relief",
    "copper": "/wallet/copper-relief",
}

CLAIM_JS = """
async (args) => {
  const r = await fetch(args.url, {
    method: 'POST',
    credentials: 'include',
    headers: args.token ? {'Content-Type': 'application/json'} : {},
    body: args.token ? JSON.stringify({'cf-turnstile-response': args.token}) : undefined,
  });
  let body = null;
  try { body = await r.json(); } catch (e) { body = {raw: await r.text()}; }
  return {status: r.status, body};
}
"""

TURNSTILE_JS = """
async (args) => {
  return await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject('render timeout 90s'), 90000);
    const el = document.createElement('div');
    el.style.cssText = 'position:fixed;left:45%;top:45%;z-index:2147483647';
    document.body.appendChild(el);
    try {
      window.turnstile.render(el, {
        sitekey: args.sitekey,
        action: args.action,
        cdata: args.cdata,
        appearance: 'always',
        callback: (tok) => { clearTimeout(t); resolve(tok); },
        'error-callback': (e) => { clearTimeout(t); reject('turnstile error: ' + e); },
        'timeout-callback': () => { clearTimeout(t); reject('turnstile timeout-callback'); },
      });
    } catch (e) {
      clearTimeout(t); reject('render threw: ' + e);
    }
  });
}
"""

DIAG_JS = """
() => ({
  hasTurnstile: !!window.turnstile,
  csp: (document.querySelector('meta[http-equiv="Content-Security-Policy"]')||{}).content || null,
  lastScripts: [...document.scripts].map(s => s.src).filter(Boolean).slice(-6),
})
"""


def load_turnstile(page):
    """用 Playwright 原生 add_script_tag 注入，比在 evaluate 里手搓 script 标签可靠。"""
    SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
    try:
        page.add_script_tag(url=SRC)
    except Exception as e:
        print("  add_script_tag 失败: %s" % e)
    try:
        # 光有 window.turnstile 不够，render 要等 api.js 初始化完才挂上
        page.wait_for_function(
            "() => !!(window.turnstile && typeof window.turnstile.render === 'function')",
            timeout=25000)
        return True
    except Exception:
        print("  turnstile 未就绪，诊断:")
        try:
            print("  ", page.evaluate(DIAG_JS))
            print("   typeof:", page.evaluate("() => typeof window.turnstile"))
        except Exception as e:
            print("   诊断也失败: %s" % e)
        return False


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="hourly", choices=list(ENDPOINTS))
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    url = API + ENDPOINTS[args.kind]
    cks = load_cookies()
    print("cookie: %s" % ", ".join(c["name"] for c in cks))
    print("引擎: %s" % ENGINE)

    common = dict(headless=args.headless)
    if ENGINE == "playwright":
        # patchright 自己处理这些，传了反而可能破坏它的补丁
        common.update(args=["--disable-blink-features=AutomationControlled"],
                      ignore_default_args=["--enable-automation"])
    try:
        p_ctx = dict(channel="chrome", **common)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(PROFILE, **p_ctx)
            run(ctx, url, args)
    except Exception as e:
        print("[!] 系统 Chrome 起不来（%s），退回 playwright chromium" % e)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(PROFILE, **common)
            run(ctx, url, args)


def run(ctx, url, args):
    ctx.add_cookies(load_cookies())
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    print("第一次尝试（无 token）…")
    res = page.evaluate(CLAIM_JS, {"url": url, "token": None})
    print("  HTTP %s %s" % (res["status"], json.dumps(res["body"], ensure_ascii=False)[:200]))

    body = res["body"] or {}
    if 200 <= res["status"] < 300:
        print("成功: claimed=%s" % body.get("claimed"))
        ctx.close()
        return 0

    err = str(body.get("error") or body.get("code") or "")
    det = body.get("details") or {}
    if "turnstile" not in err or not det.get("siteKey"):
        print("不是 turnstile 问题：%s" % err)
        ctx.close()
        return 1

    print("需要 Turnstile: sitekey=%s action=%s" % (det["siteKey"], det.get("action")))
    print("加载 turnstile 脚本…")
    if not load_turnstile(page):
        page.screenshot(path=os.path.join(HERE, "turnstile_fail.png"))
        print("已截图 turnstile_fail.png")
        ctx.close()
        return 3

    # patchright 会修补 Runtime，导致 evaluate 传函数时的执行上下文和
    # wait_for_function 不一致（明明有 turnstile，函数里却是 undefined）。
    # 所以把 render 逻辑作为页面脚本注入，evaluate 只取简单标量。
    js = """
    window.__tok = null; window.__err = null;
    (function(){
      var el = document.createElement('div');
      el.style.cssText = 'position:fixed;left:45%%;top:45%%;z-index:2147483647';
      document.body.appendChild(el);
      try {
        window.turnstile.render(el, {
          sitekey: '%s', action: '%s', cdata: '%s', appearance: 'always',
          callback: function(t){ window.__tok = t; },
          'error-callback': function(e){ window.__err = 'error:' + e; },
          'timeout-callback': function(){ window.__err = 'timeout'; }
        });
      } catch (e) { window.__err = 'throw:' + e; }
    })();
    """ % (det["siteKey"], det.get("action") or "", det.get("cData") or "")
    try:
        page.add_script_tag(content=js)
    except Exception as e:
        print("注入 render 脚本失败: %s" % e)
        ctx.close()
        return 3

    token = None
    for _ in range(95):
        try:
            token = page.evaluate("() => window.__tok")
            err = page.evaluate("() => window.__err")
        except Exception as e:
            print("轮询异常: %s" % e)
            err = None
        if token:
            break
        if err:
            print("turnstile 报错: %s" % err)
            page.screenshot(path=os.path.join(HERE, "turnstile_fail.png"))
            ctx.close()
            return 3
        page.wait_for_timeout(1000)

    if not token:
        print("90 秒没拿到 token")
        page.screenshot(path=os.path.join(HERE, "turnstile_fail.png"))
        ctx.close()
        return 3

    print("拿到 token: %s…" % token[:24])
    res2 = page.evaluate(CLAIM_JS, {"url": url, "token": token})
    print("重发 HTTP %s %s" % (res2["status"], json.dumps(res2["body"], ensure_ascii=False)[:300]))
    ok = 200 <= res2["status"] < 300
    if ok:
        print("成功: claimed=%s" % (res2["body"] or {}).get("claimed"))
    ctx.close()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
