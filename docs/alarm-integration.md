# SuiEye 告警对外接入文档

> 2026-10-19 建立。面向要把告警接进/接出 SuiEye 的外部系统（DCS / MES / 视频分析盒子 / 飞书 / 钉钉 / 企业微信）。
> 本文档描述的是**已经实现并验收过**的接口，不含规划项。

## 一、两条路，按对方能力选

| 方向                     | 方式                        | 适合谁                               |
| ---------------------- | ------------------------- | --------------------------------- |
| **别人 → SuiEye**（入站）    | `POST /api/alarms/ingest` | DCS / PLC / MES / 视频盒子，能发 HTTP 就行 |
| **SuiEye → 别人**（出站·长连） | `WS /ws/alarms`           | 能维持 WebSocket 的系统，延迟最低            |
| **SuiEye → 别人**（出站·回调） | Webhook 订阅                | 只会收 HTTP 回调的系统：飞书、钉钉、企业微信、自建平台    |

**群机器人（飞书 / 企业微信 / 钉钉）不用自己写转发层** —— 订阅时选 `provider`，
SuiEye 按对方规定的 body 结构发送，见第五节「群机器人接入」。



---

## 二、事件契约

所有推送（WS 与 Webhook）使用同一份格式：

```json
{
  "event": "alarm.created",
  "ts": "2026-10-19T16:40:09.231+08:00",
  "data": {
    "id": 460,
    "scene_id": "flotation",
    "scene_label": "浮选品位预测",
    "level": "danger",
    "level_label": "严重",
    "rule_name": "球磨机轴承温度超限",
    "message": "实测 78°C，超过上限 75°C",
    "metric": null,
    "location": null,
    "status": "new",
    "status_label": "待处理",
    "source": "external",
    "origin": "DCS",
    "group_key": "ext:DCS:B-2026-1019-001",
    "created_at": "2026-10-19T16:40:09"
  }
}
```

### 事件列表

| event           | 触发时机     | 说明                       |
| --------------- | -------- | ------------------------ |
| `connected`     | WS 连接成功  | 仅 WS，`data.channel` 是频道名 |
| `alarm.created` | 新告警产生    | 分析产生 / 外部推入 / 测试         |
| `alarm.updated` | 告警状态变更   | 确认、处置、误报、静默              |
| `alarm.test`    | 设置页点"测试" | 仅投递给被测试的那一个订阅            |
| `snapshot`      | 周期快照     | 目前只有 water 频道            |

### `source` / `origin` 的含义

- `source = internal`，`origin = "SuiEye"`：SuiEye 自己分析产生的告警
- `source = external`，`origin = "DCS"`：外部系统推入的告警

接收方应据此区分"这条到底是谁报的"。

---

## 三、入站：把告警推给 SuiEye

```
POST /api/alarms/ingest
X-API-Key: <ALARM_INGEST_KEY>
Content-Type: application/json
```

### 请求体

| 字段            | 必填 | 说明                                                      |
| ------------- | -- | ------------------------------------------------------- |
| `level`       | ✅  | `warning` 或 `danger`，其他值返回 422                          |
| `rule_name`   | ✅  | 告警名，≤100 字                                              |
| `scene_id`    |    | 工段，默认 `flotation`                                       |
| `message`     |    | 详细说明，≤2000 字                                            |
| `origin`      |    | 来源系统名，默认「外部系统」，会显示在告警卡片上                                |
| `external_id` |    | **对方系统内的唯一 id**。用于重推去重：同一 `origin + external_id` 只会入库一次 |
| `group_key`   |    | 自定义聚合键，不传则由 `external_id` 自动生成                          |
| `created_at`  |    | ISO 8601 时间。不传则用服务端收到的时间                                |
| `evidence`    |    | 任意 JSON，原样存进证据链                                         |

### 示例

```bash
curl -X POST http://127.0.0.1:8300/api/alarms/ingest \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: suieye-ingest-demo-key' \
  -d '{
    "scene_id": "flotation",
    "level": "danger",
    "rule_name": "球磨机轴承温度超限",
    "message": "实测 78°C，超过上限 75°C",
    "origin": "DCS",
    "external_id": "B-2026-1019-001"
  }'
```

### 响应

```json
{ "ok": true, "id": 460, "duplicate": false, "broadcast": true }
```

- `duplicate: true` 表示这条 `external_id` 已存在，本次没有重复入库、也没有重复推送
- `broadcast: true` 表示已向 WS 与 Webhook 派发

### 错误码

| 码   | 含义                                                   |
| --- | ---------------------------------------------------- |
| 401 | `X-API-Key` 无效或缺失                                    |
| 422 | `level` 非法 / `rule_name` 为空 / `created_at` 不是 ISO 时间 |

### 配置

`backend/config.py`：

```python
ALARM_INGEST_KEY: str = "suieye-ingest-demo-key"   # 上线前必须改
```

也可以用环境变量 `ALARM_INGEST_KEY` 覆盖。设置为空字符串 = **关闭鉴权**（不建议）。

---

## 四、出站 A：WebSocket

```
ws://<host>/ws/alarms          # 生产
ws://<host>/ws/alarms?token=X  # 若配置了 WS_TOKEN
```

- 连上后立即收到一帧 `connected`
- 之后服务端单向推送 `alarm.created` / `alarm.updated`
- **客户端需每 30 秒发一次任意文本**当心跳（服务端不解析内容，只为保活）
- 断开后请自行重连（SuiEye 前端是 3 秒后重连）
- 若服务端配置了 `WS_TOKEN` 而 token 不匹配，连接会以 **4401** 关闭

Python 示例：

```python
import asyncio, json, websockets

async def listen():
    async with websockets.connect("ws://127.0.0.1:8300/ws/alarms") as ws:
        while True:
            msg = json.loads(await ws.recv())
            if msg["event"] == "alarm.created":
                d = msg["data"]
                print(f"[{d['origin']}] {d['rule_name']} — {d['message']}")

asyncio.run(listen())
```

---

## 五、出站 B：Webhook

### 注册订阅

```bash
curl -X POST http://127.0.0.1:8300/api/webhooks \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://your-mes.example.com/suieye/alarm",
       "events":["alarm.created"],
       "description":"MES 生产管理"}'
```

响应里带 `secret`（形如 `whsec_xxx`）—— **只返回这一次**，丢了只能删除重建。

### 投递规则

| 项    | 说明                                                     |
| ---- | ------------------------------------------------------ |
| 方法   | `POST`，`Content-Type: application/json; charset=utf-8` |
| 超时   | 5 秒（`WEBHOOK_TIMEOUT`）                                 |
| 重试   | **不重试**。失败只记录，不阻塞告警主流程，也不影响其他订阅方                       |
| 成功判定 | HTTP 2xx                                               |
| 自动停用 | 连续失败 20 次且从未成功 → 自动 `enabled=false`，避免一直撞死接口           |

### 请求头

```
X-SuiEye-Event: alarm.created
X-SuiEye-Signature: sha256=<hex>
X-SuiEye-Delivery: <subscription_id>-<毫秒时间戳>
User-Agent: SuiEye-Webhook/1.0
```

### 验签（务必做）

签名是 `HMAC-SHA256(secret, 原始请求体字节)`，不是对 JSON 重新序列化后的字符串——  
**必须先拿 raw body，再验签，最后才解析 JSON**，否则空格/转义差异会导致验签失败。

Python：

```python
import hashlib, hmac, json
from flask import Flask, request, abort

app = Flask(__name__)
SECRET = "whsec_xxx"

@app.post("/suieye/alarm")
def receive():
    raw = request.get_data()                      # 原始字节，别用 request.json
    expect = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, request.headers.get("X-SuiEye-Signature", "")):
        abort(401)
    payload = json.loads(raw)
    print(payload["data"]["rule_name"])
    return {"ok": True}
```

Node：

```js
const crypto = require('crypto')
const expected = 'sha256=' + crypto.createHmac('sha256', SECRET).update(rawBody).digest('hex')
if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(sig))) return res.status(401).end()
```

### 管理接口

| 方法     | 路径                                  | 说明                          |
| ------ | ----------------------------------- | --------------------------- |
| GET    | `/api/webhooks`                     | 订阅列表（**不返回 secret**）        |
| POST   | `/api/webhooks`                     | 新建，响应里回显一次 secret           |
| PUT    | `/api/webhooks/{id}`                | 改 url / events / 描述 / 启停    |
| DELETE | `/api/webhooks/{id}`                | 删除订阅与它的投递流水                 |
| POST   | `/api/webhooks/{id}/test`           | 立刻发一条 `alarm.test`，同步返回投递结果 |
| GET    | `/api/webhooks/deliveries?limit=30` | 最近投递流水（状态码 / 耗时 / 错误）       |
| GET    | `/api/webhooks/integration`         | 设置页用的对接信息（地址、Key 脱敏、WS 在线数） |

### 群机器人接入（飞书 / 企业微信 / 钉钉）

订阅时选 `provider`，SuiEye 按对方要求的格式组装 body，**不需要自建转发层**。

| provider   | 取地址的地方                        | 消息形态                     | 签名                  | 成功判定        |
| ---------- | ----------------------------- | ------------------------ | ------------------- | ----------- |
| `feishu`   | 群 → 设置 → 群机器人 → 添加自定义机器人     | interactive 卡片（红/橙/蓝标题） | 可选，body 内 `timestamp`+`sign` | `code == 0` |
| `wecom`    | 群 → 群机器人 → 添加                | markdown                 | 不支持加签（地址已含 key）      | `errcode == 0` |
| `dingtalk` | 群 → 智能群助手 → 添加机器人 → 自定义       | markdown                 | 可选，URL 上 `timestamp`+`sign` | `errcode == 0` |
| `generic`  | 自建平台 / MES                    | 原始 `{event, ts, data}`    | `X-SuiEye-Signature` 头 | HTTP 2xx    |

#### 飞书：三步接通

1. 飞书群右上「…」→ 设置 → 群机器人 → 添加机器人 → **自定义机器人** → 复制 Webhook 地址
   （形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`）
2. 若机器人安全设置选了「签名校验」，把**密钥**一起记下来
3. SuiEye：告警页右上「**接入飞书**」→ 粘贴地址（有密钥就填右侧「加签密钥」）→ 添加订阅 → 点「测试」

群里应立刻收到一张卡片：标题「严重 · 规则名」，正文含场景 / 规则 / 内容 / 来源 / 时间 / 告警 ID。
之后每条真实告警都会自动推送，无需再配置。

#### ★ HTTP 200 不等于送达

三家的失败都藏在 body 里，HTTP 一律 200：

| 场景         | HTTP | body                                     |
| ---------- | ---- | ---------------------------------------- |
| 飞书签名错      | 200  | `{"code":19021,"msg":"sign match fail"}` |
| 企微地址失效     | 200  | `{"errcode":93000,"errmsg":"..."}`       |
| 钉钉关键词不匹配   | 200  | `{"errcode":310000,"errmsg":"..."}`      |

SuiEye 会解析业务码，非 0 记为**失败**，并在订阅行直接显示原因
（如「被对方拒收 code=19021 sign match fail」）。只看 HTTP 状态的话，
这类失败会被记成"正常 200"——**告警没到群里，界面却显示投递成功**。

#### 签名算法（三家不同，写反不报错、只是校验失败）

```python
# 飞书：key 是 "ts\nsecret"，消息体为空
ts = str(int(time.time()))
sign = base64.b64encode(
    hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
).decode()

# 钉钉：key 是 secret，消息是 "ts\nsecret"，且用毫秒 + urlencode
ts = str(round(time.time() * 1000))
sign = urllib.parse.quote_plus(base64.b64encode(
    hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
))
# 企微：机器人不支持加签
```

钉钉的 `timestamp` / `sign` 拼在 **URL 查询参数**上；飞书的放在 **body 顶层**。

---

## 六、当前限制（写清楚，免得对方按想象中的能力设计）

1. **Webhook 没有重试队列**。网络抖动丢掉就是丢掉。要可靠投递请用 WebSocket 或自行对账 `GET /api/alarms`。
2. **没有按事件类型分频道**。`alarm.created` / `alarm.updated` 走同一个 `/ws/alarms`。
3. **单机内存广播**。`_active_conns` 存在进程内存里，多副本部署时需要换成 Redis Pub/Sub 之类的总线。
4. **入站不校验 `scene_id` 是否存在**。外部系统可以推一个 SuiEye 没有的工段名，界面上会原样显示。
5. **WS 默认不鉴权**。`WS_TOKEN` 留空时任何能访问该端口的人都能订阅告警。生产环境必须配。
6. **群机器人只推告警事件**（`alarm.created` / `alarm.updated` / `alarm.test`），不推快照类事件——避免往群里刷无关消息。
7. **群机器人是单向通知**：不支持 @所有人、不支持卡片按钮回调（点了也不会回到 SuiEye）。
8. **飞书新版权限**：自定义机器人需在群设置里开启，且部分企业租户禁用了自定义机器人，需管理员放行。

---

## 七、验收脚本

| 脚本 | 覆盖什么 |
| --- | --- |
| `outputs/_qa/qa_alarm_realtime.py` | WS 连接 → ingest → 页面 3 秒内出现（实测 **231ms**）；全程**不刷新页面**（注入 marker 证明）；落库 `source`/`origin` 正确；错误 Key 401 |
| `outputs/_qa/_test_chat_adapters.py` | 飞书/企微/钉钉的 body 结构、三种签名算法、**HTTP 200 但业务码非 0 必须判失败**、真告警（非测试消息）也能推到飞书 |
| `outputs/_qa/qa_feishu_entry.py` | 告警页「接入飞书」→ 跳设置页 → 添加区滚入视口 + 高亮 + 地址框聚焦 + 平台已选飞书 |

跑法（应用已启动）：

```bash
python outputs/_qa/qa_alarm_realtime.py 9440 1440 900
python outputs/_qa/_test_chat_adapters.py          # 自带 mock 服务，不需要真 token
python outputs/_qa/qa_feishu_entry.py 9475 1440 900
```

> `_test_chat_adapters.py` 用本地 mock 模拟飞书/企微/钉钉的返回体，
> 能验证我们这一侧的格式与判定，**不能**验证对方后台的真实配置（token 有效性、签名开关）。
> 真实群仍需填一次真地址点「测试」。
