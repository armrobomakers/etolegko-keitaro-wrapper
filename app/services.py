from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError, as_list, find_by_id
from app.models import Operation, utc_now
from app.schemas import CampaignCreateInput


def slugify(value: str) -> str:
    transliteration = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    })
    value = value.lower().translate(transliteration)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return slug[:60] or "campaign"


def _is_active(item: dict[str, Any]) -> bool:
    state = str(item.get("state", item.get("status", "active"))).lower()
    return item.get("is_active", True) is not False and state not in {"disabled", "deleted", "archived", "inactive"}


def _require_choice(client: KeitaroClient, path: str, item_id: int, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    item = find_by_id(as_list(client.get(path, params=params)), item_id)
    if not item or not _is_active(item):
        raise ValueError(f"Не найден активный элемент ID {item_id} в справочнике {path}")
    return item


def _resolve_redirect_action(client: KeitaroClient, settings: Settings) -> dict[str, Any]:
    actions = as_list(client.get("streams_actions"))
    if settings.redirect_action:
        found = next((a for a in actions if settings.redirect_action in {str(a.get("key")), str(a.get("name"))}), None)
        if found:
            return found
        raise ValueError("KEITARO_REDIRECT_ACTION не совпадает с доступным действием Keitaro")
    for action in actions:
        key = str(action.get("key", "")).lower()
        name = str(action.get("name", "")).lower()
        if key in {"http", "redirect"} or "http redirect" in name or "redirect" in name:
            return action
    raise ValueError("В Keitaro не найдено действие HTTP-переадресации; задайте KEITARO_REDIRECT_ACTION")


def _action_payload(action: dict[str, Any], value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    field = str(action.get("field") or "url").strip()
    if not field:
        field = "url"
    return {field: value}


def _find_country_filter(client: KeitaroClient) -> str:
    filters = as_list(client.get("stream_filters"))
    country = next((item for item in filters if str(item.get("value", "")).lower() in {"country", "country_code"}), None)
    if country is None:
        raise ValueError("В Keitaro не найден GEO-фильтр страны")
    return str(country["value"])


def _operation_json(operation: Operation) -> dict[str, Any]:
    try:
        value = json.loads(operation.summary_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def begin_operation(db: Session, *, action: str, idempotency_key: str | None = None) -> Operation:
    operation = Operation(action=action, status="started", idempotency_key=idempotency_key, summary_json="{}")
    db.add(operation)
    db.commit()
    db.refresh(operation)
    return operation


def finish_operation(db: Session, operation: Operation, *, status: str, campaign_id: int | None = None,
                     resource_id: int | None = None, summary: dict[str, Any] | None = None,
                     error: str | None = None) -> None:
    operation.status = status
    operation.campaign_id = campaign_id
    operation.resource_id = resource_id
    operation.summary_json = json.dumps(summary or {}, ensure_ascii=False)
    operation.safe_error = (error or "")[:500] or None
    operation.updated_at = utc_now()
    db.commit()


def previous_idempotent_result(db: Session, key: str) -> tuple[Operation, dict[str, Any]] | None:
    operation = db.scalar(select(Operation).where(Operation.idempotency_key == key))
    if operation is None:
        return None
    return operation, _operation_json(operation)


def create_campaign(client: KeitaroClient, db: Session, settings: Settings, request: CampaignCreateInput,
                    idempotency_key: str) -> dict[str, Any]:
    # Validate all referenced entities and the instance capabilities before the first write.
    _require_choice(client, "domains", request.domain_id)
    _require_choice(client, "groups", request.group_id, params={"type": "campaigns"})
    _require_choice(client, "traffic_sources", request.traffic_source_id)
    offer = _require_choice(client, "offers", request.offer_id)
    country_filter = _find_country_filter(client)
    redirect_action = _resolve_redirect_action(client, settings)

    operation = begin_operation(db, action="create_campaign", idempotency_key=idempotency_key)
    campaign_id: int | None = None
    created_stream_ids: list[int] = []
    prefixed_name = settings.campaign_prefix + slugify(request.name)
    enable_attempted = False
    try:
        campaign = client.post("campaigns", {
            "name": prefixed_name,
            "alias": prefixed_name,
            "state": "disabled",
            "domain_id": request.domain_id,
            # Keitaro's CampaignRequest schema declares this field as a string.
            "group_id": str(request.group_id),
            "traffic_source_id": request.traffic_source_id,
        })
        campaign_id = int(campaign["id"])
        finish_operation(db, operation, status="started", campaign_id=campaign_id,
                         summary={"name": prefixed_name, "stream_ids": []})

        country_stream = client.post("streams", {
            "campaign_id": campaign_id,
            "schema": "redirect",
            "type": "regular",
            "name": f"GEO {', '.join(request.country_codes)} → Google",
            "position": 1,
            "state": "active",
            "action_type": redirect_action["key"],
            "action_options": _action_payload(redirect_action, "https://www.google.com/"),
            "filters": [{"name": country_filter, "mode": "accept", "payload": request.country_codes}],
        })
        created_stream_ids.append(int(country_stream["id"]))
        finish_operation(db, operation, status="started", campaign_id=campaign_id,
                         summary={"name": prefixed_name, "stream_ids": created_stream_ids})

        offer_stream = client.post("streams", {
            "campaign_id": campaign_id,
            "schema": "landings",
            "type": "default",
            "name": f"Offer {request.offer_id}: {offer.get('name', 'selected offer')}",
            "position": 2,
            "state": "active",
            "action_type": redirect_action["key"],
            "offers": [{"offer_id": request.offer_id, "share": 100, "state": "active"}],
        })
        created_stream_ids.append(int(offer_stream["id"]))
        finish_operation(db, operation, status="started", campaign_id=campaign_id,
                         summary={"name": prefixed_name, "stream_ids": created_stream_ids})

        enable_attempted = True
        client.post(f"campaigns/{campaign_id}/enable")
        final = {
            "campaign_id": campaign_id,
            "name": prefixed_name,
            "alias": campaign.get("alias", prefixed_name),
            "state": "active",
            "stream_ids": created_stream_ids,
        }
        finish_operation(db, operation, status="succeeded", campaign_id=campaign_id, summary=final)
        return final
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, (KeitaroError, ValueError, KeyError, TypeError)) else "Ошибка выполнения операции в Keitaro"
        campaign_state = "disabled" if campaign_id is not None and not enable_attempted else "unknown"
        if campaign_id is not None and enable_attempted:
            try:
                client.post(f"campaigns/{campaign_id}/disable")
                campaign_state = "disabled"
            except Exception:
                # A lost response during enable/disable is an uncertain remote state; never claim otherwise.
                campaign_state = "unknown"
        finish_operation(db, operation, status="failed", campaign_id=campaign_id,
                         summary={"name": prefixed_name, "stream_ids": created_stream_ids,
                                  "campaign_state_after_failure": campaign_state}, error=safe_error)
        raise


def offer_signature(offers: list[dict[str, Any]]) -> str:
    normalized = sorted(
        (int(item.get("offer_id", item.get("id", 0))), int(item.get("share", 0)),
         str(item.get("state", "active"))) for item in offers
    )
    raw = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def distribute_equally(offer_ids: list[int], *, state_by_id: dict[int, str] | None = None) -> list[dict[str, Any]]:
    if not offer_ids:
        return []
    ordered = list(dict.fromkeys(offer_ids))
    base, remainder = divmod(100, len(ordered))
    states = state_by_id or {}
    return [
        {"offer_id": offer_id, "share": base + (1 if index >= len(ordered) - remainder else 0),
         "state": states.get(offer_id, "active")}
        for index, offer_id in enumerate(ordered)
    ]


def normalize_offers(stream: dict[str, Any]) -> list[dict[str, Any]]:
    raw = stream.get("offers") or []
    result: list[dict[str, Any]] = []
    for item in raw:
        offer_id = item.get("offer_id", item.get("id"))
        if offer_id is None:
            continue
        result.append({"offer_id": int(offer_id), "share": int(item.get("share", 0)),
                       "state": str(item.get("state", "active"))})
    return result


def update_stream_offers(client: KeitaroClient, db: Session, *, campaign_id: int, stream_id: int,
                         offer_ids: list[int], expected_version: str) -> dict[str, Any]:
    stream = client.get(f"streams/{stream_id}")
    if int(stream.get("campaign_id", -1)) != campaign_id:
        raise ValueError("Поток не принадлежит выбранной кампании")
    if stream.get("schema") != "landings" or not isinstance(stream.get("offers"), list):
        raise ValueError("Редактировать можно только поток со схемой landings и списком офферов")
    current = normalize_offers(stream)
    if offer_signature(current) != expected_version:
        raise RuntimeError("Список офферов изменился. Обновите данные и повторите действие")
    if not offer_ids:
        raise ValueError("В потоке должен остаться хотя бы один оффер")
    if len(set(offer_ids)) != len(offer_ids):
        raise ValueError("Оффер не может встречаться в потоке дважды")
    current_by_id = {item["offer_id"]: item for item in current}
    catalog = {int(item["id"]): item for item in as_list(client.get("offers")) if item.get("id") is not None}
    states = {item["offer_id"]: item["state"] for item in current}
    for selected_id in offer_ids:
        if selected_id not in current_by_id:
            offer = catalog.get(selected_id)
            if not offer or not _is_active(offer):
                raise ValueError(f"Не найден активный оффер ID {selected_id}")
            states[selected_id] = "active"

    updated_offers = distribute_equally(offer_ids, state_by_id=states)
    operation = begin_operation(db, action="stream_offer_push")
    try:
        # Keitaro documents PUT as a partial update; sending only offers avoids overwriting filters/actions.
        client.put(f"streams/{stream_id}", {"offers": updated_offers})
        verified = client.get(f"streams/{stream_id}")
        actual = normalize_offers(verified)
        if offer_signature(actual) != offer_signature(updated_offers):
            raise KeitaroError("Keitaro принял изменение, но проверка прочитанного списка офферов не совпала")
        finish_operation(db, operation, status="succeeded", campaign_id=campaign_id,
                         resource_id=stream_id, summary={"offers": updated_offers})
        return {"offers": actual, "version": offer_signature(actual)}
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, (KeitaroError, ValueError, RuntimeError)) else "Ошибка обновления потока"
        finish_operation(db, operation, status="failed", campaign_id=campaign_id,
                         resource_id=stream_id, summary={"offer_ids": offer_ids}, error=safe_error)
        raise
