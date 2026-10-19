"""告警出站的消息格式适配（飞书 / 企业微信 / 钉钉 / 通用 JSON）

★★ 为什么必须有这一层（2026-10-19，老板："可以接入飞书等软件"）

   之前 Webhook 一律发我们自己的 `{event, ts, data}`。对自建平台没问题，
   但**群机器人只认自己那一套 body**：
     · 飞书     {"msg_type": "interactive", "card": {...}}      不认 event/ts/data → 400
     · 企业微信 {"msgtype": "markdown", "markdown": {"content"}}  同上
     · 钉钉     {"msgtype": "markdown", "markdown": {"title","text"}}
   「接入飞书」如果只是把 URL 填进来，第一条告警就会 400，设置页写着"已订阅"却一条没到。

★★ 更要紧的是成功判定：三家都是 **HTTP 200 + body 里业务码为 0**。
   飞书签名错会返回 HTTP 200 + {"code":19021,"msg":"sign match fail"}；
   钉钉/企微同理。**只判 HTTP 200 = 把"被对方拒收"记成"投递成功"**，
   告警丢了还以为推到了 —— 这是这类系统里最坏的一种假。

★★ 签名算法三家各不相同，极易记混（写反不报错，只是 sign match fail）：
     · 飞书：sign = b64(hmac_sha256(key=f"{ts}\\n{secret}", msg=b""))           放 body
     · 钉钉：sign = urlencode(b64(hmac_sha256(key=secret, msg=f"{ts}\\n{secret}"))) 拼 URL
     · 企微：机器人不支持加签，只有 URL 里的 key
   注意飞书的 key 是 "ts\\nsecret"、消息体为空；钉钉正相反（key 是 secret、消息是 "ts\\nsecret"）。

★ 内容上守两条：禁 emoji（老板要求）；没值就整行不渲染，不写"未知"占位。
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote_plus

# 等级 → 各家颜色语义
_LEVEL_COLOR = {"danger": "red", "warning": "orange", "info": "blue"}
_LEVEL_CN = {"danger": "严重告警", "warning": "预警", "info": "提示"}
_SOURCE_CN = {"internal": "SuiEye 自算", "external": "外部接入"}

PROVIDERS = {
    "generic": {
        "label": "通用 JSON",
        "hint": "自建平台/MES：发 SuiEye 原始事件结构，带 X-SuiEye-Signature 验签头",
        "need_secret": False,
        "doc": "",
        "placeholder": "https://your-mes.example.com/suieye/alarm",
    },
    "feishu": {
        "label": "飞书群机器人",
        "hint": "飞书群 → 设置 → 群机器人 → 添加自定义机器人，复制 Webhook 地址；机器人开了「签名校验」时，把密钥填进右侧加签密钥框",
        "need_secret": False,
        "doc": "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
        "placeholder": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx",
    },
    "wecom": {
        "label": "企业微信群机器人",
        "hint": "企业微信群 → 群机器人 → 添加，复制 Webhook 地址（地址里已含 key，无需 secret）",
        "need_secret": False,
        "doc": "https://developer.work.weixin.qq.com/document/path/91770",
        "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx",
    },
    "dingtalk": {
        "label": "钉钉群机器人",
        "hint": "钉钉群 → 智能群助手 → 添加机器人 → 自定义；安全设置选「加签」时，把密钥填进右侧加签密钥框",
        "need_secret": False,
        "doc": "https://open.dingtalk.com/document/robots/custom-robot-access",
        "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx",
    },
}

# 群机器人没有"我们的事件名"概念，一律只推 alarm.* 里真正对人有用的事件
_PUSHABLE = ("alarm.created", "alarm.updated", "alarm.test")


def _human_time(iso: str | None) -> str:
    """2026-10-19T16:53:20.123+08:00 → 10-19 16:53:20（群里不需要年份和时区）"""
    if not iso:
        return ""
    s = str(iso).replace("T", " ")
    return s[5:19] if len(s) >= 19 else s


def _clip(s, n: int = 300) -> str:
    """群消息里的长文本要截断：钉钉 markdown 单条超长会被整条丢弃，
    一条被丢的告警比一条被截断的告警糟糕得多（截断还能看出是什么事）。"""
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _clip(s, n: int = 300) -> str:
    """群消息里的长文本要截断：钉钉 markdown 单条超长会被整条丢弃，
    一条被丢的告警比一条被截断的告警糟糕得多（截断还能看出是什么事）。"""
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _rows(data: dict) -> list[tuple[str, str]]:
    """把告警字典摊成人读的键值行；空值整行不渲染。"""
    scene = " · ".join(
        x for x in (data.get("scene_label") or data.get("scene_id"), data.get("location")) if x
    )
    origin = data.get("origin") or "SuiEye"
    src = _SOURCE_CN.get(data.get("source") or "internal", "SuiEye 自算")
    rows = [
        ("场景", scene),
        ("规则", data.get("rule_name")),
        ("内容", _clip(data.get("message"))),
        ("来源", f"{origin}（{src}）" if data.get("source") == "external" else origin),
        ("时间", _human_time(data.get("created_at"))),
        ("告警 ID", str(data["id"]) if data.get("id") else ""),
    ]
    return [(k, str(v).strip()) for k, v in rows if v and str(v).strip()]


def _title(data: dict) -> str:
    lv = data.get("level") or "warning"
    cn = data.get("level_label") or _LEVEL_CN.get(lv, "告警")
    rule = (data.get("rule_name") or "").strip()
    return f"{cn} · {rule}" if rule else cn


def _md(data: dict, *, html_color: bool) -> str:
    """群消息正文（markdown）。html_color 只给企业微信开——它支持 <font color>。"""
    lv = data.get("level") or "warning"
    head = f"**{_title(data)}**"
    if html_color:
        color = {"danger": "warning", "warning": "warning", "info": "comment"}.get(lv, "comment")
        head = f'<font color="{color}">{head}</font>'
    body = "\n".join(f"> {k}：{v}" for k, v in _rows(data))
    return f"{head}\n{body}"


# ============================================================
# 各家的 body / 签名
# ============================================================
def _feishu_body(data: dict, secret: str) -> dict:
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _LEVEL_COLOR.get(data.get("level") or "warning", "orange"),
            "title": {"tag": "plain_text", "content": _title(data)},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(f"**{k}**：{v}" for k, v in _rows(data)),
                },
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "SuiEye 淬眸 · 铅锌浮选全流程智能视觉分析平台"}
                ],
            },
        ],
    }
    body = {"msg_type": "interactive", "card": card}
    if secret:
        ts = str(int(time.time()))
        # ★ 飞书：key 是 "ts\nsecret"，消息体为空
        key = f"{ts}\n{secret}".encode("utf-8")
        sign = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
        body["timestamp"] = ts
        body["sign"] = sign
    return body


def _dingtalk_body(data: dict, secret: str) -> tuple[dict, dict]:
    body = {
        "msgtype": "markdown",
        "markdown": {"title": _title(data), "text": _md(data, html_color=False)},
    }
    query = {}
    if secret:
        ts = str(round(time.time() * 1000))       # 钉钉要毫秒
        # ★ 钉钉：key 是 secret，消息是 "ts\nsecret"（与飞书正好相反）
        sign = quote_plus(
            base64.b64encode(
                hmac.new(secret.encode("utf-8"), f"{ts}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
            )
        )
        query = {"timestamp": ts, "sign": sign}
    return body, query


def _wecom_body(data: dict, secret: str) -> dict:
    # 企微机器人不支持加签；secret 填了也用不上，忽略
    return {"msgtype": "markdown", "markdown": {"content": _md(data, html_color=True)}}


def _with_query(url: str, query: dict) -> str:
    if not query:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + "&".join(f"{k}={v}" for k, v in query.items())


def build(provider: str, event: str, data: dict, *, secret: str = "", url: str = "") -> dict:
    """组装投递请求：返回 {url, body(bytes), headers, kind}

    kind 决定成功判定方式：
      · "json" —— 通用模式，按 HTTP 状态判定（同旧行为）
      · "chat" —— 群机器人，必须再解析 body 的业务码
    """
    p = provider if provider in PROVIDERS else "generic"

    if p == "generic":
        payload = {"event": event, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "data": data}
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return {"url": url, "body": body, "headers": {}, "kind": "json", "provider": p}

    # 群机器人只该收到告警类事件；别的（快照之类）直接不发，避免群里刷无意义消息
    if event not in _PUSHABLE:
        return {"url": "", "body": b"", "headers": {}, "kind": "skip", "provider": p}

    if p == "feishu":
        body_obj, query = _feishu_body(data, secret), {}
    elif p == "dingtalk":
        body_obj, query = _dingtalk_body(data, secret)
    else:
        body_obj, query = _wecom_body(data, secret), {}

    body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "SuiEye-Webhook/1.0"}
    return {"url": _with_query(url, query), "body": body, "headers": headers, "kind": "chat", "provider": p}


# ============================================================
# 成功判定
# ============================================================
def verify(provider: str, resp_text: str) -> tuple[bool, str | None]:
    """群机器人的业务码判定。返回 (ok, 错误描述)。

    ★ HTTP 已 2xx 时才调这里。飞书 code/StatusCode、钉钉/企微 errcode。
    """
    p = provider if provider in PROVIDERS else "generic"
    if p == "generic":
        return True, None

    txt = (resp_text or "").strip()
    if not txt:
        # 企微在极少数情况下回空体；无法确认即视为失败，宁可报错也不谎报成功
        return False, "响应体为空，无法确认送达"

    import json as _json

    try:
        obj = _json.loads(txt)
    except Exception:
        return False, f"响应非 JSON：{txt[:200]}"

    if p == "feishu":
        code = obj.get("code", obj.get("StatusCode"))
    else:
        code = obj.get("errcode")

    if code is None:
        return False, f"响应缺少业务码：{txt[:200]}"
    if int(code) == 0:
        return True, None

    msg = obj.get("msg") or obj.get("errmsg") or obj.get("StatusMessage") or ""
    return False, f"被对方拒收 code={code} {msg}".strip()


def provider_list() -> list[dict]:
    return [{"id": k, **v} for k, v in PROVIDERS.items()]
