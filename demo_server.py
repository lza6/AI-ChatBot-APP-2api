#!/usr/bin/env python3
"""AI ChatBot v10.0.0 (com.benben.bah.openal) -> OpenAI 兼容 2API 网关 (demo 版)。

上游协议（纯静态还原，无 native 依赖）:
  1) POST {VULCAN}/smith-auth/api/v1/token
     body {"device_id","order_id","product_id","purchase_token","subscription_id"}
     -> {"AccessToken": "<HS256 JWT>", ...}   免费签发, 无签名校验, 可无限重放
  2) POST {VULCAN}/smith-v2/api/v7/chat_android
     header Authorization: Bearer <JWT>   <-- 唯一硬校验 (缺失 -> 403 code 14)
     body   {"model","messages":[{"role","content"}],"user"}
     -> {"id","object","created","model","choices":[{"Message":{...}}],"usage","error"}
     注意 choices[].Message 是大写 M (非标准 OpenAI), 网关层已归一化。

免费层事实: 任何 model 名都会被服务端路由到 gpt-4o-mini-2024-07-18; 无真流式
(stream:true 返回空 body), 本地做伪流式 SSE 包装。

用法: python demo_server.py [--port 8917]
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from collections import deque

import requests
from flask import Flask, Response, jsonify, request, send_file
from requests.adapters import HTTPAdapter

VULCAN_TOKEN_URL = "https://api.vulcanlabs.co/smith-auth/api/v1/token"
VULCAN_CHAT_URL = "https://api.vulcanlabs.co/smith-v2/api/v7/chat_android"
USER_AGENT = "Chat Smith Android, Version 3.9.19(795)"
DEFAULT_MODEL = "gpt-4o-mini"
UPSTREAM_MODEL = "gpt-4o-mini-2024-07-18"

# 代理隧道并发上限是硬约束(实测 6-7 条), 固定 6 条 keep-alive 连接。
POOL_SIZE = int(os.environ.get("AICHATBOT_POOL", "6"))
TOKEN_TTL_SECONDS = 25 * 60
REQUEST_TIMEOUT = 120

DEVICE_ID = os.environ.get("AICHATBOT_DEVICE_ID") or str(uuid.uuid4())

# 免费 tier 下上游把任何 model 名一律路由到 UPSTREAM_MODEL, 所以这里只承认真实
# 可用的模型名, 其余一律 400。避免"配了 gpt-4o、实际跑 gpt-4o-mini、按 gpt-4o 计费"
# 的对账错误。
MODELS = ("gpt-4o-mini", UPSTREAM_MODEL)

app = Flask(__name__)

_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=POOL_SIZE, max_retries=0))

_token_lock = threading.Lock()
_token_cache: dict[str, object] = {"value": None, "expires_at": 0.0}

_stats_lock = threading.Lock()
_stats = {"requests": 0, "ok": 0, "failed": 0, "latency_ms": deque(maxlen=200)}
_started_at = time.time()


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _http_error_text(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:300] or resp.reason
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return json.dumps(payload, ensure_ascii=False)[:300]


def _fetch_token() -> str:
    body = {
        "device_id": DEVICE_ID,
        "order_id": "",
        "product_id": "",
        "purchase_token": "",
        "subscription_id": "",
    }
    resp = _session.post(
        VULCAN_TOKEN_URL,
        json=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        timeout=45,
    )
    if resp.status_code != 200:
        raise UpstreamError(502, f"token endpoint {resp.status_code}: {_http_error_text(resp)}")
    token = (resp.json() or {}).get("AccessToken")
    if not token:
        raise UpstreamError(502, "token endpoint returned no AccessToken")
    return token


def get_token(force: bool = False) -> str:
    """带单飞锁的 token 缓存, 避免并发请求各自刷新触发上游风控。"""
    with _token_lock:
        fresh = _token_cache["expires_at"] > time.time()
        if not force and fresh and _token_cache["value"]:
            return str(_token_cache["value"])
        token = _fetch_token()
        _token_cache["value"] = token
        _token_cache["expires_at"] = time.time() + TOKEN_TTL_SECONDS
        return token


def call_upstream(messages: list[dict]) -> dict:
    """透传一轮对话到 smith-v2 chat_android, 返回归一化后的 OpenAI 响应。"""
    payload = {"model": DEFAULT_MODEL, "messages": messages, "user": DEVICE_ID}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + get_token(),
        "User-Agent": USER_AGENT,
    }
    started = time.time()
    try:
        resp = _session.post(VULCAN_CHAT_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise UpstreamError(502, f"upstream transport: {type(exc).__name__}") from exc

    if resp.status_code == 403:
        # 唯一硬校验失败: JWT 过期或上游换签名 -> 强制刷新一次再试
        headers["Authorization"] = "Bearer " + get_token(force=True)
        try:
            resp = _session.post(VULCAN_CHAT_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise UpstreamError(502, f"upstream transport: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        raise UpstreamError(resp.status_code, _http_error_text(resp))

    try:
        data = resp.json()
    except ValueError as exc:
        raise UpstreamError(502, "upstream returned non-JSON body") from exc

    if data.get("error"):
        raise UpstreamError(502, _http_error_text(resp))

    choices = []
    for item in data.get("choices") or []:
        raw = item.get("Message") or item.get("message") or {}
        choices.append(
            {
                "index": item.get("index", 0),
                "message": {"role": raw.get("role") or "assistant", "content": raw.get("content") or ""},
                "finish_reason": item.get("finish_reason") or "stop",
            }
        )
    if not choices:
        raise UpstreamError(502, "upstream returned no choices")

    return {
        "response": {
            "id": data.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": data.get("created") or int(time.time()),
            "model": data.get("model") or UPSTREAM_MODEL,
            "choices": choices,
            "usage": data.get("usage") or {},
        },
        "latency": time.time() - started,
    }


def _record(latency: float | None, ok: bool) -> None:
    with _stats_lock:
        _stats["requests"] += 1
        if ok:
            _stats["ok"] += 1
            if latency is not None:
                _stats["latency_ms"].append(int(latency * 1000))
        else:
            _stats["failed"] += 1


def _sse_chunks(text: str, chunk_size: int = 12):
    """伪流式: 上游没有真 SSE, 按字符切片模拟打字机效果。"""
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]


@app.get("/health")
def health():
    with _stats_lock:
        lat = list(_stats["latency_ms"])
        return jsonify(
            {
                "status": "ok",
                "uptime_s": round(time.time() - _started_at, 1),
                "device_id": DEVICE_ID,
                "token_cached": bool(_token_cache["value"]),
                "upstream_model": UPSTREAM_MODEL,
                "requests": _stats["requests"],
                "ok": _stats["ok"],
                "failed": _stats["failed"],
                "p50_ms": sorted(lat)[len(lat) // 2] if lat else None,
            }
        )


@app.get("/v1/models")
def models():
    data = [
        {"id": name, "object": "model", "created": 1700000000, "owned_by": "vulcanlabs-free"}
        for name in MODELS
    ]
    return jsonify({"object": "list", "data": data})


@app.post("/v1/chat/completions")
def chat_completions():
    body = request.get_json(silent=True) or {}
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": {"message": "messages is required", "type": "invalid_request_error"}}), 400

    normalized = []
    for item in messages:
        if not isinstance(item, dict):
            return jsonify({"error": {"message": "messages items must be objects", "type": "invalid_request_error"}}), 400
        role = item.get("role")
        content = item.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            return jsonify({"error": {"message": "each message needs string role and content", "type": "invalid_request_error"}}), 400
        normalized.append({"role": role, "content": content})

    requested = body.get("model") or DEFAULT_MODEL
    if requested not in MODELS:
        return jsonify(
            {"error": {"message": f"model '{requested}' is not available", "type": "invalid_request_error"}}
        ), 400

    try:
        result = call_upstream(normalized)
    except UpstreamError as exc:
        _record(None, False)
        return jsonify({"error": {"message": exc.message, "type": "upstream_error"}}), exc.status

    data = result["response"]
    _record(result["latency"], True)

    if not body.get("stream"):
        return jsonify(data)

    content = data["choices"][0]["message"]["content"]

    def generate():
        first = {
            "id": data["id"],
            "object": "chat.completion.chunk",
            "created": data["created"],
            "model": data["model"],
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
        for piece in _sse_chunks(content):
            chunk = {
                "id": data["id"],
                "object": "chat.completion.chunk",
                "created": data["created"],
                "model": data["model"],
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        last = {
            "id": data["id"],
            "object": "chat.completion.chunk",
            "created": data["created"],
            "model": data["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": data.get("usage") or {},
        }
        yield f"data: {json.dumps(last, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.get("/")
def index():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.html"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8917)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"[demo] http://127.0.0.1:{args.port}  device_id={DEVICE_ID}")
    app.run(host=args.host, port=args.port, threaded=True)
