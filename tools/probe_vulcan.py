#!/usr/bin/env python3
"""VulcanLabs (Chat Smith 家族) 端点矩阵探测。

token 端点已知可用且可无限重放（服务端只按 device_id 签发 tier=free JWT）。
本脚本对多域名 x 多路径做带全头 / 简头两轮 POST，打印状态码与响应片段。
"""
import json
import time
import uuid
import urllib.error
import urllib.request

DEVICE_ID = "a1b2c3d4-0000-1111-2222-333344445555"
APP_ID = "com.benben.bah.openal"
UA = "Chat Smith Android, Version 3.9.19(795)"
TIMEOUT = 60

TOKEN_HOSTS = [
    "https://api.vulcanlabs.co/smith-auth/api/v1/token",
    "https://chatgpt-au.vulcanlabs.co/api/v1/token",
]
CHAT_BASES = [
    "https://api.vulcanlabs.co",
    "https://chatgpt-au.vulcanlabs.co",
    "https://prod-smith.vulcanlabs.co",
]
CHAT_PATHS = [
    "/smith-v2/api/v7/chat_android",
    "/smith-v2/api/v6/chat_android",
    "/smith-v2/api/v3/chat_android",
    "/smith-v2/api/v6/chat",
    "/api/v6/chat",
    "/api/v3/chat",
    "/api/v7/chat_android",
]


def post(url, body, headers, timeout=TIMEOUT):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - 探测场景需要看到任何异常
        return 0, f"{type(e).__name__}: {e}"


def get_token():
    body = {
        "device_id": DEVICE_ID,
        "order_id": "",
        "product_id": "",
        "purchase_token": "",
        "subscription_id": "",
    }
    for host in TOKEN_HOSTS:
        for attempt in range(3):
            code, text = post(host, body, {"Content-Type": "application/json"})
            if code == 200:
                try:
                    return host, json.loads(text)["AccessToken"]
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(1.5)
    return None, None


def main():
    host, token = get_token()
    print(f"[token] {host} -> {token[:32] if token else 'FAILED'}...")
    if not token:
        return

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "say hi in 3 words"}],
        "user": DEVICE_ID,
    }
    full_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Vulcan-Application-ID": APP_ID,
        "X-Vulcan-Request-ID": "914948789" + str(int(time.time() * 1000)),
        "Anonymous-User-Id": DEVICE_ID,
        "Authorization": "Bearer " + token,
        "X-Auth-Token": "",
        "User-Agent": UA,
    }
    slim_headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
        "User-Agent": UA,
    }

    for base in CHAT_BASES:
        for path in CHAT_PATHS:
            for label, hdrs in (("full", full_headers), ("slim", slim_headers)):
                code, text = post(base + path, payload, hdrs)
                snippet = " ".join(text.split())[:150]
                print(f"{label:4} {base + path:62} {code:>4}  {snippet}")
                if code == 200:
                    print("  ^^^ HIT")
            time.sleep(0.3)


if __name__ == "__main__":
    main()
