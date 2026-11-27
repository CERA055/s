#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线验证 step-up 流程：本地起 mock server，模拟服务端索要 Turnstile，
确认 s_claim.py 能解析 details、调 solver、回填 token 并换 UA。

    python test_stepup.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s_claim as M   # noqa: E402

SITEKEY = "0x4AAAAAAA_test_sitekey"
CDATA = "cdata-abc123"
ACTION = "turnstile-hourly"
GOOD_TOKEN = "GOODTOKEN_0123456789abcdef"
SOLVER_UA = "Mozilla/5.0 (TestSolverUA) Chrome/131.0.0.0"

state = {"claims": 0, "saw_ua": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        body = json.loads(raw) if raw.strip() else None
        path = self.path

        if path.endswith("/wallet/claim/hourly"):
            state["claims"] += 1
            # 第一次：不带 token → 索要验证
            if not body or "cf-turnstile-response" not in body:
                return self._json(403, {
                    "error": "hourly_turnstile_required",
                    "message": "整点补给需要人机验证",
                    "details": {"siteKey": SITEKEY, "cData": CDATA, "action": ACTION},
                })
            state["saw_ua"] = self.headers.get("User-Agent")
            if body["cf-turnstile-response"] != GOOD_TOKEN:
                return self._json(403, {"error": "turnstile_invalid",
                                        "message": "验证失败"})
            return self._json(200, {"claimed": 250,
                                    "wallet": {"bonusBalance": 1000, "copperBalance": 50}})

        if path.endswith("/wallet/claim/daily"):
            return self._json(200, {"claimed": 500,
                                    "wallet": {"bonusBalance": 1500, "copperBalance": 50}})
        return self._json(404, {"error": "not_found"})


class FakeSolver:
    def __init__(self):
        self.calls = []

    def solve(self, sitekey, action=None, cdata=None):
        self.calls.append((sitekey, action, cdata))
        return GOOD_TOKEN, SOLVER_UA


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    M.API = "http://127.0.0.1:%d/api" % port
    M.BASE = "http://127.0.0.1:%d" % port
    M.LOG_FILE = os.path.join(HERE, "claim.test.log")

    ck = M.COOKIE_FILE + ".test"
    open(ck, "w", encoding="utf-8-sig").write("Cookie: sid=fake; __cf_bm=xyz\n")
    M.COOKIE_FILE = ck

    s = M.build_session()
    solver = FakeSolver()

    ok, note = M.claim(s, "hourly", solver=solver)
    print("\n[1] hourly(带 solver) -> ok=%s note=%s" % (ok, note))
    assert ok and note == "claimed_after_turnstile", note
    assert solver.calls and solver.calls[0] == (SITEKEY, ACTION, CDATA), solver.calls
    assert state["saw_ua"] == SOLVER_UA, "回填时没把 UA 换成解 token 的 UA：%r" % state["saw_ua"]

    before = state["claims"]
    ok2, note2 = M.claim(s, "daily")
    print("[2] daily(无需验证) -> ok=%s note=%s" % (ok2, note2))
    assert ok2 and note2 == "claimed", note2
    assert state["claims"] == before, "daily 不该打到 hourly 分支"

    # 无 solver、无手动 token：应如实报告 turnstile_needed，而不是假装成功或崩溃
    ok3, note3 = M.claim(s, "hourly")
    print("[3] hourly(无 solver) -> ok=%s note=%s" % (ok3, note3))
    assert not ok3 and note3 == "turnstile_needed", note3

    ok4, note4 = M.claim(s, "hourly", manual_token=GOOD_TOKEN)
    print("[4] hourly(手动 token) -> ok=%s note=%s" % (ok4, note4))
    assert ok4 and note4 == "claimed_after_turnstile", note4

    srv.shutdown()
    os.remove(ck)
    print("\nALL PASS —— step-up 解析 / solver 调用 / token 回填 / UA 替换 全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
