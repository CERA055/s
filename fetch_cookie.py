#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从本机 Chromium 系浏览器（Chrome / Edge / Brave）取出指定站点的 Cookie，
并写成 s_claim.py 认的 cookie.txt。

只在你自己机器上读你自己的浏览器数据。需要同用户身份运行（DPAPI 限制）。

    python fetch_cookie.py --probe                 # 只看有没有、能不能解
    python fetch_cookie.py --write                 # 解出来写进 cookie.txt
    python fetch_cookie.py --browser chrome --write
"""

import argparse
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile

DOMAIN = "casino.soliumbra.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cookie.txt")

BROWSERS = {
    "edge":   os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\User Data"),
    "chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\User Data"),
    "brave":  os.path.join(os.environ.get("LOCALAPPDATA", ""), r"BraveSoftware\Brave-Browser\User Data"),
    "chrome-beta": os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome Beta\User Data"),
}


def list_profiles(root):
    """返回 [(profile_name, user_data_root)]，含 Default 和 Profile N。"""
    out = []
    if not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        if name == "Default" or name.startswith("Profile "):
            if os.path.isdir(os.path.join(root, name)):
                out.append(name)
    return out


def cookie_db(profile_dir):
    for p in (os.path.join(profile_dir, "Network", "Cookies"),
              os.path.join(profile_dir, "Cookies")):
        if os.path.exists(p):
            return p
    return None


def master_key(root):
    ls = os.path.join(root, "Local State")
    if not os.path.exists(ls):
        return None, "没有 Local State"
    st = json.load(open(ls, encoding="utf-8"))
    oc = st.get("os_crypt") or {}

    if oc.get("app_bound_encrypted_key"):
        # Chrome 127+ App-Bound Encryption，v20 的 key 走 COM IElevator，不是 DPAPI
        pass

    enc = oc.get("encrypted_key")
    if not enc:
        return None, "os_crypt.encrypted_key 不存在"
    blob = base64.b64decode(enc)
    if blob[:5] != b"DPAPI":
        return None, "encrypted_key 前缀不是 DPAPI: %r" % blob[:8]
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(blob[5:], None, None, None, 0)[1], None
    except Exception as e:
        return None, "DPAPI 解密失败: %s" % e


def decrypt(value, key):
    """返回 (plaintext, 状态)。状态: ok / v20_abe / empty / plain"""
    if not value:
        return "", "empty"
    head = bytes(value[:3])
    if head in (b"v10", b"v11"):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce, ct, tag = bytes(value[3:15]), bytes(value[15:-16]), bytes(value[-16:])
            return AESGCM(key).decrypt(nonce, ct + tag, None).decode("utf-8"), "ok"
        except Exception as e:
            return "", "fail:%s" % e
    if head == b"v20":
        return "", "v20_abe"
    try:
        return bytes(value).decode("utf-8"), "plain"
    except Exception:
        return "", "fail"


def _connect(db_path):
    """拿到一个可读连接。浏览器在跑时文件被占用，退回 immutable 直读。"""
    tmp = os.path.join(tempfile.gettempdir(), "ck_%d.db" % os.getpid())
    try:
        shutil.copy2(db_path, tmp)
        for ext in ("-wal", "-shm"):
            src = db_path + ext
            if os.path.exists(src):
                try:
                    shutil.copy2(src, tmp + ext)
                except OSError:
                    pass
        return sqlite3.connect(tmp), [tmp, tmp + "-wal", tmp + "-shm"]
    except OSError:
        # 被独占：直接对原文件以 immutable 只读打开（忽略 WAL 与锁）
        from pathlib import Path
        uri = Path(db_path).as_uri() + "?immutable=1"
        return sqlite3.connect(uri, uri=True), []


def read_rows(db_path, pattern):
    con, cleanup = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        q = ("select host_key, name, encrypted_value, value, path, expires_utc "
             "from cookies where host_key like ? order by host_key, name")
        return [dict(r) for r in con.execute(q, ("%" + pattern + "%",))]
    finally:
        con.close()
        for f in cleanup:
            try:
                os.remove(f)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default=None, help="edge / chrome / brave；默认全扫")
    ap.add_argument("--domain", default=DOMAIN)
    ap.add_argument("--write", action="store_true", help="写进 cookie.txt")
    ap.add_argument("--probe", action="store_true", help="只看状态")
    args = ap.parse_args()

    targets = [args.browser] if args.browser else list(BROWSERS)
    hits = []

    for b in targets:
        root = BROWSERS.get(b)
        if not root or not os.path.isdir(root):
            continue
        profiles = list_profiles(root)
        if not profiles:
            continue
        key, kerr = master_key(root)
        print("\n[%s] %s" % (b, root))
        if kerr:
            print("    master key: %s" % kerr)

        for prof in profiles:
            pdir = os.path.join(root, prof)
            db = cookie_db(pdir)
            if not db:
                continue
            try:
                rows = read_rows(db, args.domain)
            except Exception as e:
                print("    %-10s 读库失败: %s" % (prof, e))
                continue
            if not rows:
                print("    %-10s 无 %s 的 cookie" % (prof, args.domain))
                continue

            statuses = {}
            for r in rows:
                pt, st = decrypt(r["encrypted_value"], key) if key else ("", "no_key")
                statuses.setdefault(st.split(":")[0], 0)
                statuses[st.split(":")[0]] += 1
                if st == "ok":
                    hits.append((b, prof, r["host_key"], r["name"], pt))
                elif st == "v20_abe":
                    hits.append((b, prof, r["host_key"], r["name"], None))

            print("    %-10s %d 条 %s" % (prof, len(rows), statuses))
            for r in rows:
                pt, st = decrypt(r["encrypted_value"], key) if key else ("", "no_key")
                mark = "OK " if st == "ok" else st
                print("        %-28s %-16s %s" % (r["host_key"], r["name"], mark))

    if not hits:
        print("\n没找到可用的 cookie。先在浏览器里登录 %s 再来。" % args.domain)
        return 1

    usable = [h for h in hits if h[4] is not None]
    if not usable:
        print("\n找到 cookie 但全是 v20（App-Bound Encryption），需要另一套解法。")
        return 3

    if args.probe:
        print("\n可用 %d 条（--write 才会写文件）" % len(usable))
        return 0

    if args.write:
        cookie = "; ".join("%s=%s" % (h[3], h[4]) for h in usable)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("Cookie: " + cookie + "\n")
        try:
            os.chmod(OUT, 0o600)
        except OSError:
            pass
        print("\n已写入 %s（%d 条 cookie，%d 字节）" % (OUT, len(usable), len(cookie)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
