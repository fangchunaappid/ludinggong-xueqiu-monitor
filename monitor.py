#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ludinggong-xueqiu-monitor — 超级鹿鼎公雪球帖增量监控

  1. 抓取 xueqiu.com 用户时间线（UID 8790885129 = 超级鹿鼎公）；
  2. 只收原创帖（转发跳过）；
  3. 增量去重：优先雪球 id，退化 (日期, 正文) 与 (日期, 正文前30字) 双键，历史绝不重复；
  4. 新帖追加进 data/posts.json（新→旧），有新帖推送飞书（仅成功入库通知，无新帖不推）。

用法：
  XQ_COOKIE="xq_a_token=...; xqat=..." FEISHU_WEBHOOK="https://..." python3 monitor.py

环境变量：
  XUEQIU_UID      可选，默认 8790885129
  XQ_COOKIE       必填，雪球登录 Cookie（社交时间线接口必须登录态；GitHub Secrets 配置）
  FEISHU_WEBHOOK  必填，飞书自定义机器人 Webhook
"""
import base64
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data", "posts.json")
ENC = os.path.join(BASE, "data", "posts.enc.json")
KEY = os.environ.get("DATA_KEY", "").strip()
UID = os.environ.get("XUEQIU_UID", "8790885129")
COOKIE = os.environ.get("XQ_COOKIE", "").strip()
FEISHU = os.environ.get("FEISHU_WEBHOOK", "").strip()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0")
MAX_PAGES = 30          # 兜底上限；正常第 1~2 页即撞上已知旧帖
PAGE_DELAY = 1.5

# 雪球挂了阿里云 WAF：普通 urllib 的 TLS 指纹会被直接拦截，
# 必须用 curl_cffi 模拟真实 Chrome TLS 指纹（requirements.txt 提供）。
from curl_cffi import requests as _creq  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402


class ApiError(RuntimeError):
    pass


def http_json(url):
    """匿名：先 GET 首页领 WAF 通行证再打接口；有 Cookie 则直接带 Cookie 打。"""
    api_h = {
        "User-Agent": UA,
        "Referer": "https://www.xueqiu.com/u/%s" % UID,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
    }
    if COOKIE:
        s = _creq.Session(impersonate="chrome124")
        r = s.get(url, headers={**api_h, "Cookie": COOKIE}, timeout=30)
    else:
        s = _creq.Session(impersonate="chrome124")
        s.get("https://www.xueqiu.com/", headers={"User-Agent": UA}, timeout=30)
        r = s.get(url, headers=api_h, timeout=30)
    try:
        j = r.json()
    except Exception:
        raise ApiError("非JSON响应(HTTP %s): %r" % (r.status_code, r.text[:120]))
    if j.get("error_description") or j.get("error_code"):
        raise ApiError("HTTP %s: %s" % (r.status_code, j.get("error_description")))
    return j


def fetch_page(page):
    """真接口：www 域 v4（旧 xueqiu.com/statuses/user/timeline.json 已 404 下线）。"""
    url = "https://www.xueqiu.com/v4/statuses/user_timeline.json?page=%d&user_id=%s" % (page, UID)
    errs = []
    for attempt in range(2):
        try:
            j = http_json(url)
            if j.get("statuses"):
                return j
            errs.append("空列表")
        except ApiError as e:
            errs.append(str(e)[:100])
        time.sleep(2)
    raise RuntimeError("接口失败: " + " | ".join(errs))


def clean_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s).strip()


def norm(s):
    return re.sub(r"\s+", "", s)


def ts_date(sec):
    v = sec or 0
    if v > 1e12:  # 部分 v4 条目时间戳是毫秒
        v /= 1000.0
    return time.strftime("%Y-%m-%d", time.localtime(v))


def feishu(text):
    if not FEISHU:
        print("(未配置 FEISHU_WEBHOOK，跳过通知)")
        return
    body = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
    req = urllib.request.Request(FEISHU, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print("飞书通知失败:", e)


def main():
    if COOKIE:
        print("已配置 XQ_COOKIE，登录态抓取（可翻深页）")
    else:
        print("未配置 XQ_COOKIE，匿名抓取（第 1 页，日常增量足够）")

    if not os.path.exists(DATA) and os.path.exists(ENC):
        if not KEY:
            print("缺 DATA_KEY，无法解密底库")
            feishu("⚠️ 超级鹿鼎公雪球监控失败：缺 DATA_KEY，无法解密底库")
            sys.exit(1)
        decrypt_data_file()
    with open(DATA, encoding="utf-8") as f:
        db = json.load(f)
    posts = db["posts"]
    known_ids = {str(p.get("id")) for p in posts if p.get("id")}
    known_keys = {(p["date"], norm(p["text"])) for p in posts}
    known_keys30 = {(p["date"], norm(p["text"])[:30]) for p in posts}

    new_posts = []
    hit_known = False
    pages_fetched = 0
    for page in range(1, MAX_PAGES + 1):
        try:
            j = fetch_page(page)
        except Exception as e:
            print("第 %d 页抓取失败: %s" % (page, e))
            feishu("⚠️ 超级鹿鼎公雪球监控失败：第 %d 页抓取异常 %s，请检查 XQ_COOKIE 是否过期" % (page, e))
            sys.exit(1)
        sts = j.get("statuses") or []
        pages_fetched += 1
        if not sts:
            print("第 %d 页为空，停止" % page)
            break
        for s in sts:
            own = clean_text(s.get("description") or s.get("text") or "")
            if s.get("retweeted_status"):
                # 转发式回复（回复@xxx: ...）保留；无自己评论的纯转发才跳过
                if re.sub(r"\s+", "", own) in ("", "转发微博", "Repost"):
                    continue
            sid = str(s.get("id") or "")
            text = own
            date = ts_date(s.get("created_at") or 0)
            if not sid or not text or not date:
                continue
            key = (date, norm(text))
            key30 = (date, norm(text)[:30])
            if sid in known_ids or key in known_keys or key30 in known_keys30:
                hit_known = True
                continue
            known_ids.add(sid)
            known_keys.add(key)
            known_keys30.add(key30)
            new_posts.append({"id": sid, "date": date, "text": text})
        if hit_known:
            print("第 %d 页撞上已知旧帖，停止" % page)
            break
        time.sleep(PAGE_DELAY)

    if not new_posts:
        print("抓取完成：翻 %d 页，新增雪球帖 0 条" % pages_fetched)
        print("无新帖，不推送。")
        return

    merged = posts + new_posts
    merged.sort(key=lambda p: p["date"], reverse=True)  # 稳定排序，同日保持原序
    db["posts"] = merged
    db["meta"]["count"] = len(merged)
    db["meta"]["date_min"] = merged[-1]["date"]
    db["meta"]["date_max"] = merged[0]["date"]
    db["meta"]["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    if KEY:
        encrypt_data_file()

    dates = sorted({p["date"] for p in new_posts})
    span = dates[0] if len(dates) == 1 else "%s ~ %s" % (dates[0], dates[-1])
    print("抓取完成：翻 %d 页，新增雪球帖 %d 条（%s），共 %d 条"
          % (pages_fetched, len(new_posts), span, len(merged)))
    feishu("✅ 已成功将 %d 条超级鹿鼎公的雪球帖加入帖子库（posts.json）（%s）"
           % (len(new_posts), span))


# ---------------------------------------------------------------- 加密层
# 仓库只存密文 posts.enc.json（AES-GCM-256 + PBKDF2-SHA256），口令在 DATA_KEY；
# 明文 posts.json 仅存在于运行内存/本地，绝不入库。浏览器端用 WebCrypto 解密。
def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 250000, dklen=32)


def encrypt_data_file():
    with open(DATA, "r", encoding="utf-8") as f:
        obj = json.load(f)
    salt, iv = os.urandom(16), os.urandom(12)
    ct, tag = AES.new(_derive_key(KEY, salt), AES.MODE_GCM, nonce=iv).encrypt_and_digest(
        json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    blob = {"v": 1, "kdf": "PBKDF2-SHA256", "iters": 250000,
            "salt": base64.b64encode(salt).decode(),
            "iv": base64.b64encode(iv).decode(),
            "ct": base64.b64encode(ct).decode(),
            "tag": base64.b64encode(tag).decode()}
    tmp = ENC + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False)
    os.replace(tmp, ENC)


def decrypt_data_file():
    with open(ENC, "r", encoding="utf-8") as f:
        d = json.load(f)
    key = _derive_key(KEY, base64.b64decode(d["salt"]))
    pt = AES.new(key, AES.MODE_GCM, nonce=base64.b64decode(d["iv"]), mac_len=16)\
        .decrypt_and_verify(base64.b64decode(d["ct"]), base64.b64decode(d["tag"]))
    with open(DATA, "w", encoding="utf-8") as f:
        f.write(pt.decode("utf-8"))


if __name__ == "__main__":
    if "--decrypt-only" in sys.argv:
        decrypt_data_file(); print("已解密", DATA)
    elif "--encrypt-only" in sys.argv:
        encrypt_data_file(); print("已加密", ENC)
    else:
        main()

