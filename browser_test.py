#!/usr/bin/env python3
"""Browser 真实用户路径验证: demo.html 走完整聊天 + 流式 + 多轮 + 错误路径。

覆盖:
  1. 页面加载、/health 状态徽章变绿
  2. 模型下拉填充
  3. 发一条消息 -> 流式打字机输出完整
  4. 第二轮追问 -> 多轮上下文命中(记住数字)
  5. 关闭流式 -> 一次性输出
  6. 无 console error / page error
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8917"
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        page.goto(BASE, wait_until="networkidle")
        check("页面标题", "2API Demo" in page.title(), page.title())

        page.wait_for_selector("#status.badge.ok", timeout=15000)
        check("健康徽章", "在线" in page.inner_text("#status"), page.inner_text("#status"))

        options = page.eval_on_selector_all("#model option", "els => els.map(e => e.value)")
        check("模型下拉", len(options) >= 3, str(options[:4]))

        page.fill("#input", "记住数字 7，只回复 好的")
        page.click("#send")
        page.wait_for_function(
            "() => { const m = document.querySelectorAll('.msg.ai'); return m.length>0 && m[m.length-1].innerText.length>6; }",
            timeout=120000,
        )
        first = page.inner_text(".msg.ai:last-of-type")
        check("第一轮流式回复", "好" in first, first.replace("\n", " ")[:60])

        page.fill("#input", "刚才的数字是几？只回复数字")
        page.click("#send")
        page.wait_for_function(
            "() => document.querySelectorAll('.msg.ai').length >= 2 && "
            "document.querySelectorAll('.msg.ai')[1].innerText.includes('7')",
            timeout=120000,
        )
        second = page.evaluate("() => document.querySelectorAll('.msg.ai')[1].innerText")
        check("多轮上下文", "7" in second, second.replace("\n", " ")[:60])

        page.uncheck("#stream")
        page.fill("#input", "只回复：非流式OK")
        page.click("#send")
        page.wait_for_function(
            "() => document.querySelectorAll('.msg.ai').length >= 3 && "
            "document.querySelectorAll('.msg.ai')[2].innerText.length > 6",
            timeout=120000,
        )
        third = page.evaluate("() => document.querySelectorAll('.msg.ai')[2].innerText")
        check("非流式回复", "OK" in third, third.replace("\n", " ")[:60])

        page.screenshot(path="evidence_demo.png", full_page=True)
        check("无 console error", not console_errors, str(console_errors[:2]))
        browser.close()

    print(f"\n===== BROWSER {'PASS' if not FAILS else 'FAIL ' + str(FAILS)} =====")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
