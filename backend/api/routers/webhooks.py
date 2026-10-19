"""告警出站 —— Webhook 订阅与投递（把告警推给其他软件）

★★ 2026-10-19 新增（老板：告警系统也要能接入其他软件）

   出站的两条路分工：
     · WebSocket `/ws/alarms` —— 给能维持长连接的系统（实时性最好，零配置）
     · Webhook（本文件）      —— 给只会收 HTTP 回调的系统（钉钉 / 企业微信 / MES / 自建平台）
   两条都留，对方按自己的能力选，不用我们替它决定。

★ 投递原则（演示版也要守）：
   1. **绝不同步等**：对方可能是 1~2 秒才回的慢接口，投递一律 create_task 异步跑，
      失败不影响分析请求、不影响 WS 广播
   2. **可验证**：带 HMAC-SHA256 签名，接收方可确认"这条确实来自 SuiEye、没被篡改"
   3. **可审计**：每次投递写一条流水（状态码 / 耗时 / 错误），设置页能看见"到底推没推出去"

★ 接收方如何验签（写进对接文档，别让对方猜）：
     body = 原始请求体字节
     expect = "sha256=" + hmac_sha256(secret, body).hexdigest()
     若 expect != 请求头 X-SuiEye-Signature → 丢弃
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.routers.ws import EVENTS, channel_stats
from backend.config import settings
from backend.models.database import (
    SessionLocal,
    WebhookDelivery,
    WebhookSubscription,
    get_db,
)
from backend.services import chat_adapters

router = APIRouter()

DEFAULT_EVENTS = ["alarm.created"]


# ============================================================
# 工具
# ============================================================
def _iso(dt) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _split_events(raw: str | None) -> list[str]:
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def _valid_events(events: list[str]) -> list[str]:
    """只接受登记过的事件名。写错事件名 = 订阅永远收不到，必须当场拒掉。"""
    bad = [e for e in events if e not in EVENTS or e in ("connected", "snapshot")]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"未知事件 {bad}；可选：{[e for e in EVENTS if e.startswith('alarm.')]}",
        )
    return events or DEFAULT_EVENTS


def _to_out(s: WebhookSubscription) -> dict:
    """对外序列化 —— **绝不返回 secret**，只告诉前端"配没配"。"""
    p = s.provider or "generic"
    return {
        "id": s.id,
        "url": s.url,
        "provider": p,
        "provider_label": chat_adapters.PROVIDERS.get(p, {}).get("label", "通用 JSON"),
        "events": _split_events(s.events),
        "description": s.description or "",
        "enabled": bool(s.enabled),
        "has_secret": bool(s.secret),
        "last_status": s.last_status,
        "last_error": s.last_error,
        "last_attempt_at": _iso(s.last_attempt_at),
        "ok_count": s.ok_count or 0,
        "fail_count": s.fail_count or 0,
        "created_at": _iso(s.created_at),
    }


def _valid_provider(p: str | None) -> str:
    v = (p or "generic").strip().lower()
    if v not in chat_adapters.PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"未知接收方类型 {v}；可选：{list(chat_adapters.PROVIDERS)}",
        )
    return v


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ============================================================
# 投递
# ============================================================
async def _deliver(sub: dict, event: str, data: dict) -> dict:
    """向单个订阅投递一条事件，返回投递结果（不抛异常）。

    ★ provider 决定 body 结构与签名位置（见 services/chat_adapters.py）；
      群机器人还要再解析业务码 —— HTTP 200 不等于送达。
    """
    provider = (sub.get("provider") or "generic").lower()
    req = chat_adapters.build(
        provider, event, data, secret=sub.get("secret") or "", url=sub["url"]
    )
    if req["kind"] == "skip":
        # 群机器人只该收到告警类事件（快照之类不往群里刷），静默跳过、不记失败
        return {"ok": True, "status": None, "error": None, "elapsed_ms": 0, "skipped": True}

    body = req["body"]
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "SuiEye-Webhook/1.0",
        "X-SuiEye-Event": event,
        "X-SuiEye-Delivery": f"{sub['id']}-{int(time.time() * 1000)}",
        **req["headers"],
    }
    # 只有通用模式走头部验签；飞书/钉钉的签名已按各自算法放进 body / URL
    if provider == "generic" and sub.get("secret"):
        headers["X-SuiEye-Signature"] = _sign(sub["secret"], body)

    t0 = time.perf_counter()
    status_code, ok, err = None, False, None
    try:
        async with httpx.AsyncClient(timeout=settings.WEBHOOK_TIMEOUT) as cli:
            resp = await cli.post(req["url"], content=body, headers=headers)
            status_code = resp.status_code
            ok = 200 <= resp.status_code < 300
            if not ok:
                err = (resp.text or "")[:400]
            elif req["kind"] == "chat":
                # ★ HTTP 200 只说明"对方收到了"，还得看它认不认这个 body / 签名
                ok, why = chat_adapters.verify(provider, resp.text)
                if not ok:
                    err = why
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:400]

    elapsed = int((time.perf_counter() - t0) * 1000)
    _record(sub["id"], event, data, status_code, ok, err, elapsed, provider)
    return {"ok": ok, "status": status_code, "error": err, "elapsed_ms": elapsed}


def _record(sub_id: int, event: str, data: dict, status_code, ok: bool, err, elapsed: int,
            provider: str = "generic"):
    """写投递流水 + 更新订阅的运行状态。

    ★ 用独立 session：dispatch 跑在后台任务里，和请求的 session 没关系，
      不能复用（否则请求一结束就被关掉）。
    """
    payload = {"event": event, "provider": provider, "data": data}
    db = SessionLocal()
    try:
        db.add(WebhookDelivery(
            subscription_id=sub_id,
            event=event,
            payload=payload,
            status_code=status_code,
            ok=ok,
            error=err,
            elapsed_ms=elapsed,
        ))
        sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == sub_id).first()
        if sub:
            sub.last_status = status_code
            sub.last_error = err
            sub.last_attempt_at = datetime.now()
            if ok:
                sub.ok_count = (sub.ok_count or 0) + 1
            else:
                sub.fail_count = (sub.fail_count or 0) + 1
            # 连续失败太多次就自动停用，避免每次告警都去撞一个已经死掉的接口
            if (sub.fail_count or 0) >= 20 and (sub.ok_count or 0) == 0:
                sub.enabled = False
        db.commit()

        # 裁剪流水（只留最近 N 条）
        keep = max(20, settings.WEBHOOK_KEEP_DELIVERIES)
        total = db.query(WebhookDelivery).count()
        if total > keep:
            cutoff_id = (
                db.query(WebhookDelivery.id)
                .order_by(WebhookDelivery.id.desc())
                .offset(keep)
                .limit(1)
                .scalar()
            )
            if cutoff_id:
                db.query(WebhookDelivery).filter(WebhookDelivery.id <= cutoff_id).delete()
                db.commit()
    except Exception as e:
        print(f"[webhook] 记录投递失败: {e}")
        db.rollback()
    finally:
        db.close()


async def dispatch_event(event: str, data: dict):
    """把事件派发给所有订阅了它的接收方（并发，互不阻塞）。

    ★ 由 `ws.emit()` 在广播完 WS 之后 create_task 调用。
    """
    db = SessionLocal()
    try:
        subs = (
            db.query(WebhookSubscription)
            .filter(WebhookSubscription.enabled.is_(True))
            .all()
        )
        targets = [
            {"id": s.id, "url": s.url, "secret": s.secret or "", "provider": s.provider or "generic"}
            for s in subs
            if event in _split_events(s.events)
        ]
    finally:
        db.close()

    if not targets:
        return
    await asyncio.gather(
        *[_deliver(t, event, data) for t in targets],
        return_exceptions=True,
    )


# ============================================================
# 入站对接信息（设置页展示用）
# ============================================================
@router.get("/api/webhooks/integration")
async def integration_info():
    """给设置页的"告警对外接入"面板：地址、钥匙状态、在线连接数。

    ★ 必须注册在 `/api/webhooks/{sub_id}` 之前，否则 "integration" 会被当 id 解析。
    """
    key = settings.ALARM_INGEST_KEY or ""
    masked = (key[:5] + "·" * 6 + key[-4:]) if len(key) > 10 else ("已配置" if key else "未配置")
    stats = channel_stats()
    return {
        "ingest_url": "/api/alarms/ingest",
        "ingest_method": "POST",
        "ingest_key_masked": masked,
        "ingest_key_set": bool(key),
        "ingest_curl": (
            "curl -X POST http://127.0.0.1:8300/api/alarms/ingest "
            "-H 'Content-Type: application/json' "
            f"-H 'X-API-Key: {key or '<your-key>'}' "
            "-d '{\"scene_id\":\"flotation\",\"level\":\"danger\","
            "\"rule_name\":\"球磨机轴承温度\",\"message\":\"78°C 超上限\","
            "\"origin\":\"DCS\"}'"
        ),
        "ws_url": "/ws/alarms",
        "ws_token_required": bool((settings.WS_TOKEN or "").strip()),
        "ws_online": stats,
        "events": [e for e in EVENTS if e.startswith("alarm.")],
        # 出站可选接收方（飞书/企微/钉钉/通用），前端下拉直接用这份，别在前端硬编码
        "providers": chat_adapters.provider_list(),
    }


# ============================================================
# 订阅 CRUD
# ============================================================
class WebhookIn(BaseModel):
    url: str
    provider: str = "generic"      # generic / feishu / wecom / dingtalk
    events: list[str] = DEFAULT_EVENTS
    secret: str | None = None      # 留空则自动生成，创建响应里回显一次
    description: str = ""
    enabled: bool = True


class WebhookPatch(BaseModel):
    url: str | None = None
    provider: str | None = None
    events: list[str] | None = None
    description: str | None = None
    enabled: bool | None = None


@router.get("/api/webhooks")
def list_webhooks(db: Session = Depends(get_db)):
    rows = db.query(WebhookSubscription).order_by(WebhookSubscription.id.desc()).all()
    return {"subscriptions": [_to_out(r) for r in rows], "total": len(rows)}


@router.post("/api/webhooks")
def create_webhook(body: WebhookIn, db: Session = Depends(get_db)):
    url = (body.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url 必须以 http:// 或 https:// 开头")
    provider = _valid_provider(body.provider)
    events = _valid_events(body.events)

    # ★ 只有通用模式自动生成 secret（我们是签名方，接收方拿它验签）。
    #   群机器人的 secret 是"对方后台开的加签密钥"，我们不能编一个 ——
    #   编了就等于强制加签，飞书/钉钉会直接 sign match fail。
    given = (body.secret or "").strip()
    if provider == "generic":
        secret = given or ("whsec_" + secrets.token_hex(16))
    else:
        secret = given

    sub = WebhookSubscription(
        url=url,
        provider=provider,
        events=",".join(events),
        secret=secret,
        description=(body.description or "").strip()[:200],
        enabled=bool(body.enabled),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    out = _to_out(sub)
    # secret 只在这一刻回显一次：之后任何接口都不再返回，丢了只能重建
    out["secret"] = secret
    return out


@router.get("/api/webhooks/deliveries")
def list_deliveries(limit: int = 30, db: Session = Depends(get_db)):
    """最近投递流水 —— "到底推没推出去"的证据。"""
    limit = max(1, min(limit, 200))
    rows = (
        db.query(WebhookDelivery)
        .order_by(WebhookDelivery.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "deliveries": [
            {
                "id": r.id,
                "subscription_id": r.subscription_id,
                "event": r.event,
                "ok": bool(r.ok),
                "status_code": r.status_code,
                "error": r.error,
                "elapsed_ms": r.elapsed_ms,
                "at": _iso(r.created_at),
            }
            for r in rows
        ]
    }


@router.put("/api/webhooks/{sub_id}")
def update_webhook(sub_id: int, body: WebhookPatch, db: Session = Depends(get_db)):
    sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")

    if body.url is not None:
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="url 必须以 http:// 或 https:// 开头")
        sub.url = url
    if body.provider is not None:
        sub.provider = _valid_provider(body.provider)
    if body.events is not None:
        sub.events = ",".join(_valid_events(body.events))
    if body.description is not None:
        sub.description = body.description.strip()[:200]
    if body.enabled is not None:
        sub.enabled = bool(body.enabled)
        if body.enabled:                    # 手动启用时把失败计数清零，给它一次干净的机会
            sub.fail_count = 0

    db.commit()
    db.refresh(sub)
    return _to_out(sub)


@router.delete("/api/webhooks/{sub_id}")
def delete_webhook(sub_id: int, db: Session = Depends(get_db)):
    sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    db.query(WebhookDelivery).filter(WebhookDelivery.subscription_id == sub_id).delete()
    db.delete(sub)
    db.commit()
    return {"ok": True, "id": sub_id}


@router.post("/api/webhooks/{sub_id}/test")
async def test_webhook(sub_id: int, db: Session = Depends(get_db)):
    """立刻发一条测试事件，**同步返回结果** —— 设置页点"测试"要马上看到成功或失败。

    ★ 走的是和真实告警完全相同的 `_deliver`，包括 provider 的 body 结构与业务码判定。
      测试按钮如果另写一套"只发 HTTP 就算成功"，就会骗人。
    """
    sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")

    target = {
        "id": sub.id,
        "url": sub.url,
        "secret": sub.secret or "",
        "provider": sub.provider or "generic",
    }
    data = {
        "id": None,
        "level": "warning",
        "level_label": "连通性测试",
        "rule_name": "测试消息",
        "message": "这条来自 SuiEye 设置页的测试按钮，收到即代表回调地址可用",
        "scene_label": "系统设置",
        "source": "internal",
        "origin": "SuiEye",
        "created_at": _now(),
        "test": True,
    }
    result = await _deliver(target, "alarm.test", data)
    return {**result, "url": sub.url, "provider": target["provider"]}
