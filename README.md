# AI ChatBot v10.0.0 Pro — 逆向 + 2API 网关

把 `com.benben.bah.openal`（VulcanLabs **Chat Smith** 白标换皮）的上游协议还原成 **OpenAI 兼容接口**，可直接作为 new-api 渠道后端。

> 本项目仅用于协议研究与本地技术验证。上游为第三方生产服务，请勿高频施压、勿分发凭证。

## 状态

| 通道 | 权重 | 端点 | 状态 |
|------|------|------|------|
| **WidgetLab / Vulcan** | 1000 | `api.vulcanlabs.co/smith-v2/api/v7/chat_android` | ✅ **已打通** |
| ChatOn | 10000 | `api.chaton.ai/chats/stream` | ⛔ 需 native 动态分析 |
| ChatKey | 1000 | `chat.emoji-keyboard.com/api/v2/Chat` | ⛔ 需 native 动态分析 |
| AIR | 100 | `aichat.zagtechnology.com/chat-ai-api/chatbot-api.php` | ⚠️ 上游 500（服务端故障，路由存在） |
| Aritek | 1 | `chatai.aritek.app/stream` | ⚠️ 401，内置假 JWT，判断已废弃 |

## 快速开始

```bash
pip install flask requests
python demo_server.py --port 8917
# 浏览器打开 http://127.0.0.1:8917
```

```bash
curl http://127.0.0.1:8917/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好"}]}'
```

## 协议（两步）

**1) 取 token** — 服务端按 `device_id` 白送 HS256 JWT，无签名校验、可无限重放：

```http
POST https://api.vulcanlabs.co/smith-auth/api/v1/token
Content-Type: application/json

{"device_id":"<任意 UUID>","order_id":"","product_id":"","purchase_token":"","subscription_id":""}
```

**2) 聊天** — 唯一硬校验头是 `Authorization: Bearer`（缺失返回 `403 {"error":{"message":"Invalid Signature","code":14}}`）：

```http
POST https://api.vulcanlabs.co/smith-v2/api/v7/chat_android
Content-Type: application/json
Authorization: Bearer <AccessToken>

{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好"}],"user":"<device_id>"}
```

响应里 `choices[].Message` 是**大写 M**（非标准 OpenAI），网关已归一化。

## 免费层真实能力

- **所有 model 名一律被上游路由到 `gpt-4o-mini-2024-07-18`**，本仓库只暴露这一个真实模型，其它名字返回 400（避免"配高端模型、按高端计费"的对账错误）
- **无真流式**：`stream:true` 上游返回空壳，网关做伪流式 SSE 包装
- **无限流**：单 device 连续 30 次 + 10 并发全 200，零 429
- 上下文 20,000 字符仍 200

## 验证数据

```
stress_test.py   12/12 PASS, 42.0s
  L1 /health 200@20并发  → 652 rps, p50 28.5ms, 0 err
  L2 基线 5/5 p50 1109ms | 并发5 5/5 | 并发10 10/10 | 连续15x2 全绿
  L3 边界 400/400/400/404 + 伪流式 SSE + 多轮上下文

browser_test.py  BROWSER PASS（Playwright 真浏览器）
  流式 1720ms / 多轮追问答出"7" 1107ms / 非流式 870ms / 无 console error
```

## new-api 接入

| 字段 | 值 |
|------|-----|
| 渠道类型 | OpenAI |
| Base URL | `http://<网关IP>:8917` |
| 密钥 | 任意非空 |
| 模型 | `gpt-4o-mini` |
| 超时 | ≥ 120s |
| 流式 | 支持（伪流式 SSE） |

## 文件

```
demo_server.py      OpenAI 兼容网关（token 单飞缓存 / 6 连接池 / 伪流式 / 错误映射）
demo.html           中文聊天页
stress_test.py      三层压测
browser_test.py     Playwright 真实用户路径验证
tools/probe_vulcan.py  端点矩阵探针（一键复验协议是否仍有效）
ANALYSIS.md         完整逆向报告（通道拓扑 / Firebase RC / native XOR 0x5A 还原）
evidence_demo.png   浏览器截图
```

## 复验协议是否失效

```bash
python tools/probe_vulcan.py
```

上游改版后该脚本会打印各端点最新状态码，便于快速判断哪一步断了。

## License

MIT
