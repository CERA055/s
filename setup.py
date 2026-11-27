#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地配置向导。凭证只写进本机文件，不经过任何外部服务。

    python setup.py

做完这四件事：
  1. 收 Cookie（支持直接粘贴 DevTools 里「Copy as cURL」的整段）
  2. 立刻打一次 /api/wallet 验证，401 就当场告诉你
  3. 写入 cookie.txt（权限收紧）
  4. 可选配打码平台 key 到 solver_keys.json
"""

import json
import os
import re
import sys

try:
    import requests
except ImportError:
    sys.exit("先装依赖: pip install requests")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s_claim as M  # noqa: E402

CURL_COOKIE = re.compile(r"""-H\s+['"]cookie:\s*([^'"]+)['"]""", re.I)
BARE_COOKIE = re.compile(r"^\s*(cookie:\s*)?(.+?=.+)$", re.I | re.S)


def read_paste():
    print("把 Cookie 粘进来。两种都认：")
    print("  A) DevTools → Network → 右键请求 → Copy as cURL，整段贴")
    print("  B) 只贴 Cookie 头的值，例如 sid=...; __cf_bm=...; cf_clearance=...")
    print("（粘贴完按一次回车；cURL 多行的话，最后留空行再回车）\n")
    lines = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)
        joined = "\n".join(lines)
        if CURL_COOKIE.search(joined):
            break
        if len(lines) == 1 and "curl" not in joined.lower():
            break                      # 单行裸 cookie
        if len(lines) > 40:
            break
    return "\n".join(lines).strip()


def extract(raw):
    m = CURL_COOKIE.search(raw)
    if m:
        return m.group(1).strip()
    # 整段 cURL 但没有 -H cookie，可能用了 -b
    m = re.search(r"""-b\s+['"]([^'"]+)['"]""", raw)
    if m:
        return m.group(1).strip()
    m = BARE_COOKIE.match(raw)
    if m:
        return m.group(2).strip()
    return raw.strip()


def verify(cookie):
    s = M.build_session.__wrapped__ if hasattr(M.build_session, "__wrapped__") else None
    # 直接用传进来的 cookie 建 session，不落盘
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": M.UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": M.BASE,
        "Referer": M.PAGE_URL,
    })
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        sess.cookies.set(k.strip(), v.strip(), domain="casino.soliumbra.com", path="/")
    return M.get_wallet(sess)


def main():
    print("=== casino.soliumbra.com 本地配置 ===\n")

    raw = read_paste()
    if not raw:
        sys.exit("没收到内容")
    cookie = extract(raw)
    if "=" not in cookie:
        sys.exit("这看着不像 Cookie 头：%r" % cookie[:80])

    names = [p.split("=", 1)[0].strip() for p in cookie.split(";") if "=" in p]
    print("\n解析到 %d 个 cookie: %s" % (len(names), ", ".join(names)))
    if not any(n in ("cf_clearance", "__cf_bm") for n in names):
        print("!  没有 cf_clearance / __cf_bm —— 不影响登录，但更容易被 hourly 索要 Turnstile")

    print("\n正在验证…")
    wallet, err = verify(cookie)
    if wallet is None:
        print("\n验证失败: %s" % err)
        print("\n→ 回浏览器重新登录 casino.soliumbra.com，再抓一次。")
        print("  注意必须是登录后的 api 请求，不是静态资源。")
        return 1

    print("验证通过。")
    print("  赠金 %s / 铜板 %s" % (wallet.get("bonusBalance"), wallet.get("copperBalance")))
    for label, field in (("每日签到", "dailyReward"), ("整点补给", "hourlyReward"),
                         ("救济金", "reliefReward"), ("游乐铜板", "copperReliefReward")):
        print("  %s 面额 %s" % (label, wallet.get(field)))

    with open(M.COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write("Cookie: " + cookie + "\n")
    try:
        os.chmod(M.COOKIE_FILE, 0o600)
    except OSError:
        pass
    print("\n已写入 %s" % M.COOKIE_FILE)

    print("\n--- 打码平台（可跳过，回车跳过）---")
    print("hourly 可能被 Turnstile 拦。有 CapSolver 账号就填 key，没有直接回车。")
    key = input("CapSolver key: ").strip()
    if key:
        data = {}
        if os.path.exists(M.KEY_FILE):
            try:
                data = json.load(open(M.KEY_FILE, encoding="utf-8-sig"))
            except (OSError, ValueError):
                data = {}
        data["capsolver"] = key
        with open(M.KEY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(M.KEY_FILE, 0o600)
        except OSError:
            pass
        print("已写入 %s" % M.KEY_FILE)
    else:
        print("跳过。之后可以手动建 solver_keys.json，或设 CAPSOLVER_KEY 环境变量。")

    print("\n下一步：")
    print("  python s_claim.py --check")
    print("  python s_claim.py --kind daily")
    print("  python s_claim.py --kind hourly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
