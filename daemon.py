#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长跑守护：在 GitHub Actions 里连续跑数小时，自己按整点领，不依赖 cron 准点。

为什么这么做：GitHub 的 schedule 在这个仓库上实测 11 小时只触发 1 次，
每小时唤醒一次这种需求它扛不住。改成「一次唤醒连续干 5.5 小时」，
对 cron 的可靠性要求就从「每小时成功」降到「每 5.5 小时成功一次」。

    python daemon.py --hours 5.5
    python daemon.py --hours 0.05      # 短跑自测
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s_claim as M  # noqa: E402


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def next_tick(lead):
    """下一个整点后 lead 秒。"""
    now = datetime.now()
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return nxt + timedelta(seconds=lead)


def build_solver():
    keys = M.load_solver_keys()
    provider = "capsolver" if keys.get("capsolver") else (
        "yescaptcha" if keys.get("yescaptcha") else None)
    if not provider:
        return None
    log("打码平台: %s" % provider)
    return M.Solver(provider, keys[provider])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.5, help="总运行时长")
    ap.add_argument("--lead", type=int, default=90, help="整点后多少秒开始领")
    args = ap.parse_args()

    started = time.time()
    deadline = started + args.hours * 3600
    log("守护启动，计划运行 %.2f 小时，结束于 %s"
        % (args.hours, datetime.fromtimestamp(deadline).strftime("%m-%d %H:%M:%S")))

    s = M.build_session()
    solver = build_solver()
    rounds = 0

    while True:
        if time.time() >= deadline - 30:
            log("到点了，收工。共 %d 轮" % rounds)
            break

        rounds += 1
        log("--- 第 %d 轮 ---" % rounds)

        auth_failed = False
        for kind in ("hourly", "daily"):
            try:
                ok, note = M.claim(s, kind, solver=solver)
            except Exception as e:
                log("%s 异常: %s" % (M.LABELS[kind], e))
                continue
            if note == "auth":
                auth_failed = True
                break

        if auth_failed:
            log("cookie 失效，退出让 workflow 报警")
            return 2

        # 睡到下一个整点后 lead 秒
        dt = datetime.now()
        tick = next_tick(args.lead)
        wait = (tick - dt).total_seconds()
        if wait < 60:
            wait += 3600
        remain = deadline - time.time()
        if wait > remain:
            wait = max(0, remain - 10)
        log("睡 %.0f 秒，到 %s 再动"
            % (wait, (dt + timedelta(seconds=wait)).strftime("%H:%M:%S")))
        time.sleep(wait)

    return 0


if __name__ == "__main__":
    sys.exit(main())
