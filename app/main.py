from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.keitaro import KeitaroClient, KeitaroError, as_list
from app.models import Operation
from app.schemas import CampaignCreateInput, CampaignView, OfferCommitInput, StreamView
from app.services import create_campaign, offer_signature, previous_idempotent_result, update_stream_offers

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Keitaro Wrapper", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_keitaro() -> Generator[KeitaroClient, None, None]:
    try:
        client = KeitaroClient(get_settings())
    except KeitaroError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        yield client
    finally:
        client.close()


def _int_id(item: dict[str, Any]) -> int:
    try:
        return int(item["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Keitaro вернул объект без корректного ID") from exc


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item.get("id"), "name": item.get("name") or item.get("domain") or item.get("alias") or f"ID {item.get('id')}",
            "state": item.get("state", item.get("status", "active"))}


def _safe_api_error(exc: KeitaroError) -> HTTPException:
    code = 503 if exc.status_code in (401, 403, 402) else 502
    return HTTPException(status_code=code, detail=str(exc))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/api/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {"status": "ok", "keitaro_configured": settings.keitaro_configured}


@app.get("/api/catalog")
def catalog(client: KeitaroClient = Depends(get_keitaro), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    try:
        domains = as_list(client.get("domains"))
        groups = as_list(client.get("groups", params={"type": "campaigns"}))
        sources = as_list(client.get("traffic_sources"))
        offers = as_list(client.get("offers"))
        active = lambda rows: [_public_item(row) for row in rows if str(row.get("state", "active")).lower() == "active"]
        return {"domains": active(domains), "groups": active(groups), "traffic_sources": active(sources),
                "offers": active(offers), "campaign_prefix": settings.campaign_prefix,
                "admin_campaign_url": settings.keitaro_base_url.rstrip("/") + "/admin/#!/campaigns/"}
    except KeitaroError as exc:
        raise _safe_api_error(exc) from exc


@app.get("/api/campaigns", response_model=list[CampaignView])
def campaigns(client: KeitaroClient = Depends(get_keitaro)) -> list[dict[str, Any]]:
    try:
        rows = as_list(client.get("campaigns"))
        return [{"id": _int_id(row), "name": str(row.get("name", "")), "alias": row.get("alias"), "state": row.get("state")} for row in rows]
    except KeitaroError as exc:
        raise _safe_api_error(exc) from exc


@app.get("/api/campaigns/{campaign_id}/streams", response_model=list[StreamView])
def campaign_streams(campaign_id: int, client: KeitaroClient = Depends(get_keitaro)) -> list[dict[str, Any]]:
    try:
        raw_streams = as_list(client.get(f"campaigns/{campaign_id}/streams"))
        offers_catalog = {str(row.get("id")): row.get("name", f"ID {row.get('id')}") for row in as_list(client.get("offers"))}
        output = []
        for stream in raw_streams:
            stream_id = _int_id(stream)
            raw_offers_value = stream.get("offers")
            raw_offers = raw_offers_value if isinstance(raw_offers_value, list) else []
            normalized = []
            for entry in raw_offers:
                offer_id = entry.get("offer_id", entry.get("id"))
                if offer_id is None:
                    continue
                normalized.append({"offer_id": int(offer_id), "name": str(offers_catalog.get(str(offer_id), f"ID {offer_id}")),
                                   "share": int(entry.get("share", 0)), "state": str(entry.get("state", "active"))})
            editable = stream.get("schema") == "landings" and isinstance(raw_offers_value, list)
            output.append({"id": stream_id, "campaign_id": int(stream.get("campaign_id", campaign_id)),
                           "name": str(stream.get("name", f"Поток {stream_id}")), "schema": str(stream.get("schema", "")),
                           "type": str(stream.get("type", "")), "updated_at": stream.get("updated_at"),
                           "offers": normalized, "version": offer_signature(raw_offers) if raw_offers else offer_signature([]),
                           "editable": editable,
                           "edit_reason": None if editable else "Доступны только потоки схемы landings со списком офферов"})
        return output
    except KeitaroError as exc:
        raise _safe_api_error(exc) from exc


@app.post("/api/campaigns", status_code=201)
def campaign_create(body: CampaignCreateInput, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                    db: Session = Depends(get_db), client: KeitaroClient = Depends(get_keitaro),
                    settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(status_code=400, detail="Для создания кампании требуется заголовок Idempotency-Key")
    previous = previous_idempotent_result(db, idempotency_key)
    if previous:
        operation, summary = previous
        if operation.status == "succeeded":
            return {**summary, "replayed": True}
        message = "Предыдущая операция ещё выполняется" if operation.status == "started" else (
            "Предыдущая операция завершилась ошибкой; кампания могла остаться отключённой. Проверьте Keitaro перед новой попыткой."
        )
        raise HTTPException(status_code=409, detail=message)
    try:
        return create_campaign(client, db, settings, body, idempotency_key)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Такой Idempotency-Key уже используется") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeitaroError as exc:
        raise _safe_api_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Не удалось завершить создание кампании") from exc


@app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/offers/push")
def edit_stream_offers(campaign_id: int, stream_id: int, body: OfferCommitInput,
                       db: Session = Depends(get_db), client: KeitaroClient = Depends(get_keitaro)) -> dict[str, Any]:
    try:
        return update_stream_offers(client, db, campaign_id=campaign_id, stream_id=stream_id,
                                    offer_ids=body.offer_ids, expected_version=body.expected_version)
    except KeitaroError as exc:
        raise _safe_api_error(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/operations")
def recent_operations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(Operation).order_by(Operation.created_at.desc()).limit(20)).all()
    result = []
    for row in rows:
        try:
            summary = json.loads(row.summary_json or "{}")
        except ValueError:
            summary = {}
        result.append({"id": row.id, "action": row.action, "status": row.status, "campaign_id": row.campaign_id,
                       "resource_id": row.resource_id, "summary": summary, "error": row.safe_error,
                       "created_at": row.created_at.isoformat() if row.created_at else None})
    return result
