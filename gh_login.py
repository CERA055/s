#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
驱动 `gh auth login` 的设备码流程，把 one-time code 抓出来。

gh 的 web flow 是：打印 code → 等回车 → 开浏览器 → 轮询直到你授权。
本脚本自动喂回车，并把 code 落到 gh_login.log，方便拿去粘。

    python gh_login.py
"""

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "gh_login.log")
GH_CANDIDATES = [
    r"C:\Program Files\GitHub CLI\gh.exe",
    r"C:\Program Files (x86)\GitHub CLI\gh.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\GitHub CLI\gh.exe"),
    os.path.join(HERE, "tools", "gh", "bin", "gh.exe"),
]

CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
DEADLINE = 300


def find_gh():
    for p in GH_CANDIDATES:
        if p and os.path.exists(p):
            return p
    from shutil import which
    return which("gh")


def main():
    gh = find_gh()
    if not gh:
        print("找不到 gh.exe")
        return 1

    env = dict(os.environ, GH_PROMPT_DISABLED="0", NO_COLOR="1", CLICOLOR="0")
    refresh = "--refresh" in sys.argv
    cmd = ([gh, "auth", "refresh", "--hostname", "github.com", "-s", "workflow"]
           if refresh else
           [gh, "auth", "login", "--hostname", "github.com",
            "--git-protocol", "https", "--web", "--skip-ssh-key"])
    print("命令: " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace", env=env,
    )

    started = time.time()
    pressed = False
    code = None
    done = False

    with open(LOG, "w", encoding="utf-8") as log:
        while True:
            if time.time() - started > DEADLINE:
                print("\n[超时] %.0f 秒内没完成授权" % DEADLINE)
                break
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
                continue

            log.write(line)
            log.flush()
            print(line, end="", flush=True)

            m = CODE_RE.search(line)
            if m and not code:
                code = m.group(1)
                print("\n>>> DEVICE CODE: %s\n" % code, flush=True)

            low = line.lower()
            if ("press enter" in low or "按回车" in line) and not pressed:
                pressed = True
                try:
                    proc.stdin.write("\n")
                    proc.stdin.flush()
                except Exception:
                    pass

            if "logged in as" in low or "已登录" in line or "authentication complete" in low:
                done = True
                break

    rc = proc.poll()
    if rc is None:
        if done:
            # 关键：gh 打印完成后还要把 token 写进 hosts/凭据库。
            # 立刻 terminate 会导致授权成功但凭据丢失，必须等它自己退出。
            print("\n等 gh 写完凭据…", flush=True)
            try:
                proc.wait(timeout=30)
                rc = proc.returncode
                print("gh 已退出 rc=%s" % rc)
            except subprocess.TimeoutExpired:
                print("[!] gh 30 秒没退出，强制结束")
                proc.terminate()
        else:
            try:
                proc.terminate()
            except Exception:
                pass

    if code:
        with open(os.path.join(HERE, "gh_code.txt"), "w", encoding="utf-8") as f:
            f.write(code + "\n")
        print("\ncode 已存 gh_code.txt")
    print("结果: %s" % ("授权完成" if done else "未完成"))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
