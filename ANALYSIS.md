# AI ChatBot v10.0.0 Pro — 逆向 + 2API 交付报告（R1）

- **APK**: `AI ChatBot_v10.0.0_Pro.apk`（45.6 MB，签名 `KAKATHIC`，非官方包）
- **包名**: `com.benben.bah.openal`（源码包 `com.benben.openal`）
- **技术栈**: 原生 Android（Kotlin + Retrofit/OkHttp），5 dex，arm64/armv7 双 ABI
- **工作目录**: `D:/apk-analysis/aichatbot10/`
- **结论**: ✅ **Vulcan 通道已打通并交付 OpenAI 兼容网关**；AIR 通道上游 500；ChatKey/ChatOn 通道需 native 动态分析（R2）

---

## 1. 白标关系：这不是独立 App

App 的真实身份是 **Chat Smith（VulcanLabs）的白标换皮**，代码里到处是原厂痕迹：

| 证据 | 位置 |
|------|------|
| `android_version` 默认值 `"Chat Smith Android, Version 3.9.19(795)"` | `WidgetLabSetting.java` |
| `chatDomain` 默认 `https://prod-smith.vulcanlabs.co` | `ChatConfig.java:110` |
| `release_version_vulan = "7.1.2"` | Remote Config `chat_config` |
| 图片生成走第三方 UMagic SDK（`assets/dic`、`assets/style/style.json`） | `ArtRepository` |

同时它还并存另外两个白标的后端（`obfPkg` 常量 `com.ask.anything.openai.chatgpt`），说明同一套代码被多包复用。

---

## 2. 上游通道拓扑（5 条，按权重）

`SettingChatConfig` 里的 `pecent_*` 是流量权重。线上实际值：

| 通道 | 权重 | Base URL | 端点 | 鉴权 | R1 状态 |
|------|------|----------|------|------|---------|
| **WidgetLab/Vulcan** | 1000 | `api.vulcanlabs.co` | `/smith-v2/api/v7/chat_android` | JWT（免费签发） | ✅ **已打通** |
| ChatOn | **10000** | `https://api.chaton.ai/` | `chats/stream` | native HMAC 头 | ⛔ 需动态 |
| ChatKey | 1000 | `https://chat.emoji-keyboard.com` | `/api/v1/RegisterUser` + `/api/v2/Chat` | `md5(deviceId + cipherKey)`，cipherKey 在 native | ⛔ 需动态 |
| AIR | 100 | `https://aichat.zagtechnology.com` | `/chat-ai-api/chatbot-api.php` | 无（body 仅 `input`） | ⚠️ 上游 500 |
| Aritek | 1 | `https://chatai.aritek.app` | `/stream` | Bearer（App 内置的是假 JWT） | ⚠️ 401，疑似废弃 |

> 权重最高的是 ChatOn，但它的 `Authorization` 由 `TokenUtil.getTokenX2(context, method, path, body, millis, packageName.hashCode())` 生成 —— 纯 native 签名，dex 层不可复现。

---

## 3. 配置下发：Firebase Remote Config 明文可读（关键突破）

App 的 token/密钥/通道开关**不在 APK 里**，而是运行时从 Firebase Remote Config 拉取（键名 `n` / `chat_config` / `setting_chat_config`）。

从 `res/values/strings.xml` 直接取得项目三要素：

```
project_id   = chat-ai-v1
google_app_id= 1:991741144979:android:5aff7de2d35e4f1cf98266
google_api_key= <redacted>
```

用匿名 `app_instance_id` 调 `firebaseremoteconfig.googleapis.com/v1/projects/chat-ai-v1/namespaces/firebase:fetch` 即可拿到全部配置，**无需任何账号**。实际拿到的敏感项：

```jsonc
// chat_config
{"secret_key":"<redacted>","token_key":"admin","time_exp":5,
 "pecent_chat_askai":0,"pecent_chat_aritek":1,"pecent_chat_air":10000000,"use_api_token":false}

// setting_chat_config
{"pecent_chat_on":10000,"pecent_chat_key":1000,"pecent_chat_widget_lab":1000,
 "widgetlab_setting":{"secret_key":"<redacted>","token_key":"admin",
                      "model":"gpt-4o-mini","provider":"openai","useNewConfig":true},
 "chatkey_setting":{"user_agent":"aichat Android 170","version":"170"}}
```

**这份配置就是免费额度的来源**：`enable_free_chat=false`、`first_open_free_mesage_number=5` —— 客户端软限 5 条，服务端不校验，因此 2API 不受该限制。

### JWT 本地自签算法（备用路径）

`defpackage/o12.java` 实现了完整 HS256 签发：
`header + "." + b64({token,exp}) + "." + b64(HMAC-SHA256(secretKey, header.payload))`，
Base64 用 `NO_WRAP`（标准表，非 urlsafe）。密钥即配置里的 `<redacted:vulcan-secret>` / `<redacted:widgetlab-secret>`。
本 R1 直接用服务端 `/api/v1/token` 签发（更省事），自签留作断供时的兜底。

---

## 4. 已打通协议：Vulcan smith-v2

### 4.1 取 token（免费、无限重放）

```http
POST https://api.vulcanlabs.co/smith-auth/api/v1/token
Content-Type: application/json

{"device_id":"<任意 UUID>","order_id":"","product_id":"","purchase_token":"","subscription_id":""}
```

响应：

```json
{"AccessToken":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
 "AccessTokenExpiration":"...","RefreshToken":"","RefreshTokenExpiration":"..."}
```

JWT claims 为 `{"token":"admin","tier":"free","exp":<+30min>,"iss":"Vulcanlabs"}` —— **服务端按 device_id 白送**，随机 UUID 即可，实测 4/4 成功。

> 另一域名 `chatgpt-au.vulcanlabs.co/api/v1/token` 同样发 token（但无聊天端点，仅 token 服务存活）；
> `prod-smith.vulcanlabs.co` 已 DNS 失效（NXDOMAIN）。`api.vulcanlabs.co` 是唯一完整可用的。

### 4.2 聊天

```http
POST https://api.vulcanlabs.co/smith-v2/api/v7/chat_android
Content-Type: application/json
Authorization: Bearer <AccessToken>      ← 唯一硬校验

{"model":"gpt-4o-mini",
 "messages":[{"role":"user","content":"你好"}],
 "user":"<device_id>"}
```

响应（**注意 `Message` 是大写 M，不是标准 OpenAI 的 `message`**）：

```json
{"id":"chatcmpl-...","object":"chat.completion","created":1793229555,
 "model":"gpt-4o-mini-2024-07-18",
 "choices":[{"Message":{"role":"assistant","content":"..."},"index":0,"finish_reason":"stop"}],
 "usage":{"prompt_tokens":15,"completion_tokens":22,"total_tokens":37},
 "error":null}
```

### 4.3 鉴权头最小集（实测逐项剔除）

| 头 | 是否必需 | 缺失后果 |
|----|---------|---------|
| `Authorization: Bearer` | ✅ 必需 | `403 {"error":{"message":"Invalid Signature","code":14}}` |
| `X-Vulcan-Application-ID` | ❌ 可省 | 无影响（200） |
| `Anonymous-User-Id` | ❌ 可省 | 无影响（200） |
| `X-Vulcan-Request-ID` | ❌ 可省 | 无影响（200） |
| `User-Agent` | ❌ 可省 | 无影响 |

→ 网关只发 2 个头即可，兼容性门槛极低。

---

## 5. Native 层：XOR 0x5A 混淆表已完整还原

`libsecurity.so` 的 `JNI_OnLoad` 流把常量解密进 `.bss`。静态还原方法：符号表定位 `Java_com_provider_token_key_TokenUtil_getUrl1/2`、`getToken` → 顺着 `adrp/ldr [x21,#off]` 找到 GOT 项 → `.rela.dyn` 取 `r_info_sym` → 拿到符号 `obfPkg/obfPkg2/funTk/url1/url2/keySo` → 在 `.init_array` 函数（`0x2374c`）里找到 `adr` 指向的密文 → 逐字节 `XOR 0x5A`：

| 符号 | 解密结果 |
|------|---------|
| `obfPkg` | `com.ask.anything.openai.chatgpt` |
| `obfPkg2` | `com.benben.bah.openal` |
| `funTk` | `com/aiai/mix_gptext/NativeUtl` |
| `url1` | `https://api.chaton.ai/` |
| `url2` | `https://chat.emoji-keyboard.com` |
| `keySo` | **未在 init 中出现**（另一条 native 链 `liblib_native.so::getSecuritySo` 填充） |

`libnative-lib.so` 另有一套 `NativeLibUtils.getToken/getKey/getIv`（`SecurityHelper` 用）。

**`keySo` 就是 ChatKey 通道缺的那把 DES/HMAC 密钥** —— 它只在运行时通过 `getSecuritySo()` 落盘到 `.bss`，纯静态取不到。这是 R1 唯一卡住的环节。

---

## 6. 免费层真实能力边界

| 项目 | 实测结论 |
|------|---------|
| 模型 | **所有 model 名一律被路由到 `gpt-4o-mini-2024-07-18`**（测 gpt-4o/gpt-5.2/claude-sonnet-4.5/gemini-2.5-pro/deepseek-chat，响应 `model` 字段全部为 gpt-4o-mini） |
| 流式 | **无真流式**。`stream:true` 返回空壳 JSON，网关层做伪流式 SSE |
| 限流 | **无**。单 device 连续 30 次、10 并发全 200，零 429 |
| 上下文 | 20,000 字符 prompt 仍 200（未触顶） |
| device 池 | 随机 UUID 4/4 发 token + 聊天成功 → 可不做池，需要时按 UUID 现造 |
| 计费 | 无任何配额字段，`tier:free` 恒定 |

---

## 7. 交付物与验证证据

### 文件

```
demo_server.py      OpenAI 兼容网关（:8917），含 token 单飞缓存 / 6 连接池 / 伪流式 / 错误映射
demo.html           中文聊天页（流式打字机、多轮上下文、健康面板）
stress_test.py      三层压测（本地吞吐 / 真实推理 / 边界）
browser_test.py     Playwright 真实用户路径验证
tools/probe_vulcan.py  端点矩阵探针
evidence_demo.png  浏览器截图证据
```

### 压测结果（`python stress_test.py`，12/12 PASS，42.0s）

```
L1 /health 200 请求 @20 并发 → 652 rps, p50 28.5ms, 0 error
L2 单请求基线 x5            → 5/5, p50 1109ms, max 1867ms
L2 并发 x5                  → 5/5, wall 1846ms
L2 并发 x10                 → 10/10, wall 1958ms, p50 1377ms
L2 连续压 15x2 轮           → 15/15 + 15/15（零 429 零 502）
L3 空 body / 未知模型 / 非法 content / 未知路由 → 400/400/400/404
L3 伪流式 SSE               → 3 chunks 正常
L3 多轮上下文               → 正确答出 "42"
```

### 浏览器验证（`python browser_test.py` → BROWSER PASS）

页面加载 / 健康徽章变绿 / 模型下拉填充 / 第一轮流式回复 1720ms / 多轮追问答出 "7" 1107ms / 非流式 870ms / 无 console error。截图见 `evidence_demo.png`。

### 未通过/未验证项（诚实标注）

- **AIR 通道**：`POST /chat-ai-api/chatbot-api.php`（FastAPI 后端）任何 body 都返回 500。GET 返回 `405 Method "GET" not allowed` 证明路由存在，是**上游自身故障**，非鉴权问题。**待验证：等上游恢复后复测**。
- **Aritek 通道**：`/stream` 返回标准 OpenAI 网关 401，App 内置 token 是伪造串 `<App 内置伪 JWT，已打码>`，带 Bearer 也被拒。**判断该通道已废弃**（权重仅 1）。
- **ChatKey 通道**：`chat.emoji-keyboard.com` 存活，`RegisterUser` 返回 `{"ret":500,"msg":"Authentication Failed 2"}`。尝试 md5(deviceId) / +UA / +version / +secretKey 四种候选全部失败 → **确认需要 native `keySo`**，需 R2 动态分析（Frida/Unidbg）。
- 本机有代理隧道并发上限（6-7 条）的既有环境约束，网关固定 6 条 keep-alive 连接规避；生产部署在无此约束的机器上可放大连接池。

---

## 8. new-api 接入参数

| 字段 | 值 |
|------|-----|
| 渠道类型 | OpenAI 兼容 |
| Base URL | `http://<网关IP>:8917` |
| 密钥 | 任意非空（如 `sk-any`） |
| 模型 | 仅 `gpt-4o-mini` / `gpt-4o-mini-2024-07-18`；其它名字返回 400（上游会静默降级，不做别名以免计费对账错误） |
| 计费 | 建议按 gpt-4o-mini 单价配置；usage 字段上游真实返回可用 |
| 流式 | 支持（网关伪流式）；若渠道开启 `stream` 需允许 SSE |
| 超时 | ≥ 120s（实测 p50 ≈ 1.1s，长 prompt 2.5s） |
| 重试 | 建议 1 次；403 网关内部已自动刷新 token 重试一次 |

---

## 9. 剩余风险

1. **上游会变**：`smith-v2/api/v7/chat_android` 属逆向路径，VulcanLabs 改版即失效。协议探针 `tools/probe_vulcan.py` 可一键复验。
2. **模型降级不可绕过**：免费 tier 已确认服务端强制路由，不存在"传高端模型拿高端模型"的路径（与 Chat Smith v8.251229.2 结论一致）。
3. **无并发上限但无 SLA**：上游不限流 ≠ 永不限流。生产承载需观察 429/403 出现点。
4. **ChatOn（权重最高那条）未打通**：纯 native 签名，若后续 Vulcan 断供需 R2 动态补齐。
5. **合规**：本仓库仅用于协议研究与本地验证，上游为第三方生产服务，不做高频施压、不再分发凭证。
