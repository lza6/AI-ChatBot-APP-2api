#!/usr/bin/env python3
"""第一轮本地压测: demo_server.py (OpenAI 兼容层) + 上游 vulcanlabs 真实链路。

分三层:
  L1 本地吞吐    /health 高并发, 不含上游
  L2 真实推理    阶梯并发 + 连续压, 走完 token -> chat_android 全链路
  L3 边界        错误路径 / 伪流式 / 多轮上下文

用法: python stress_test.py [--base http://127.0.0.1:8917]
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time

import requests

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def timed(method: str, url: str, **kwargs):
    started = time.time()
    resp = requests.request(method, url, timeout=kwargs.pop("timeout", 180), **kwargs)
    return resp, (time.time() - started) * 1000


def scene_local_throughput(base: str, total: int = 200, concurrency: int = 20) -> None:
    print(f"\n== L1 /health 吞吐 ({total} 请求 / {concurrency} 并发) ==")
    lat: list[float] = []
    errors = 0
    lock = threading.Lock()

    def worker(count: int) -> None:
        nonlocal errors
        for _ in range(count):
            try:
                _, ms = timed("GET", base + "/health", timeout=10)
                with lock:
                    lat.append(ms)
            except Exception:  # noqa: BLE001
                with lock:
                    errors += 1

    started = time.time()
    per = total // concurrency
    threads = [threading.Thread(target=worker, args=(per,)) for _ in range(concurrency)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.time() - started
    rps = total / wall
    p50 = statistics.median(lat) if lat else 0
    record("L1 health", errors == 0 and rps > 50, f"{rps:.0f} rps, p50 {p50:.1f}ms, err {errors}")


def one_chat(base: str, prompt: str, model: str = "gpt-4o-mini") -> tuple[bool, float, str]:
    try:
        resp, ms = timed(
            "POST",
            base + "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        if resp.status_code != 200:
            return False, ms, f"HTTP {resp.status_code} {resp.text[:80]}"
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
        return bool(text), ms, text[:40]
    except Exception as exc:  # noqa: BLE001
        return False, 0.0, f"{type(exc).__name__}: {exc}"


def scene_baseline(base: str, rounds: int = 5) -> None:
    print(f"\n== L2 单请求基线 x{rounds} ==")
    lat, fails = [], 0
    for i in range(rounds):
        ok, ms, detail = one_chat(base, f"第{i + 1}题：只回复数字 {i + 1}")
        lat.append(ms)
        if not ok:
            fails += 1
        print(f"    {ms:7.0f}ms {'ok' if ok else 'fail'} {detail}")
    record(
        "L2 基线",
        fails == 0,
        f"{rounds - fails}/{rounds} 成功, p50 {statistics.median(lat):.0f}ms, max {max(lat):.0f}ms",
    )


def scene_concurrency(base: str, concurrency: int) -> None:
    print(f"\n== L2 并发 x{concurrency} ==")
    out: dict[int, tuple[bool, float, str]] = {}
    lock = threading.Lock()

    def worker(idx: int) -> None:
        ok, ms, detail = one_chat(base, f"并发测试 {idx}: 只回复 OK")
        with lock:
            out[idx] = (ok, ms, detail)

    started = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = (time.time() - started) * 1000
    ok_n = sum(1 for v in out.values() if v[0])
    lat = [v[1] for v in out.values() if v[0]]
    record(
        f"L2 并发 x{concurrency}",
        ok_n == concurrency,
        f"{ok_n}/{concurrency} 成功, wall {wall:.0f}ms, p50 {statistics.median(lat):.0f}ms"
        + ("" if ok_n == concurrency else f", 失败: {[v[2] for v in out.values() if not v[0]][:2]}"),
    )


def scene_burst(base: str, count: int = 15, rounds: int = 1) -> None:
    print(f"\n== L2 连续压 {count}x{rounds} 轮 ==")
    for r in range(rounds):
        ok_n, lat, codes = 0, [], set()
        for i in range(count):
            ok, ms, detail = one_chat(base, f"连续第{r + 1}-{i + 1}次：回复 OK")
            if ok:
                ok_n += 1
                lat.append(ms)
            else:
                codes.add(detail[:40])
        record(
            f"L2 连续轮{r + 1}",
            ok_n == count,
            f"{ok_n}/{count} 成功, p50 {statistics.median(lat):.0f}ms" if lat else f"0/{count} {codes}",
        )


def scene_boundary(base: str) -> None:
    print("\n== L3 边界 ==")
    resp, _ = timed("POST", base + "/v1/chat/completions", json={})
    record("L3 空 body", resp.status_code == 400, f"HTTP {resp.status_code}")

    resp, _ = timed(
        "POST",
        base + "/v1/chat/completions",
        json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    record("L3 未知模型", resp.status_code == 400, f"HTTP {resp.status_code}")

    resp, _ = timed(
        "POST",
        base + "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": 123}]},
    )
    record("L3 非法 content 类型", resp.status_code == 400, f"HTTP {resp.status_code}")

    resp, _ = timed("GET", base + "/nope")
    record("L3 未知路由", resp.status_code == 404, f"HTTP {resp.status_code}")

    # 伪流式: 校验 SSE 帧结构 + [DONE]
    chunks, text = 0, ""
    with requests.post(
        base + "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "从1数到8"}], "stream": True},
        stream=True,
        timeout=180,
    ) as resp:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            delta = json.loads(payload)["choices"][0]["delta"]
            if delta.get("content"):
                chunks += 1
                text += delta["content"]
    record("L3 伪流式 SSE", chunks > 1 and bool(text), f"{chunks} chunks, text={text[:30]!r}")

    # 多轮上下文
    msgs = [
        {"role": "user", "content": "记住数字 42，只回复 好的"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "刚才的数字是多少？只回复数字"},
    ]
    resp, _ = timed("POST", base + "/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": msgs})
    if resp.status_code == 200:
        answer = resp.json()["choices"][0]["message"]["content"]
        record("L3 多轮上下文", "42" in answer, f"回答 {answer[:30]!r}")
    else:
        record("L3 多轮上下文", False, f"HTTP {resp.status_code}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8917")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print(f"目标 {base}")
    t0 = time.time()
    scene_local_throughput(base)
    scene_baseline(base)
    scene_concurrency(base, 5)
    scene_concurrency(base, 10)
    scene_burst(base, 15, 2)
    scene_boundary(base)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n===== 汇总 {passed}/{len(RESULTS)} 通过, 总耗时 {time.time() - t0:.1f}s =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL {name}: {detail}")


if __name__ == "__main__":
    main()
