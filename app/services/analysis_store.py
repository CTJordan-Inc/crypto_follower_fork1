from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SavedAnalysisSnapshot
from app.services.performance import resolve_market_cap_from_response
from app.utils.normalizers import normalize_address

settings = get_settings()


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def _make_json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, default=_json_default))


def save_analysis_snapshot(
    db: Session,
    payload: dict[str, Any],
    top_n_tokens: int,
) -> SavedAnalysisSnapshot:
    address = normalize_address(payload["address"])
    snapshot = db.scalar(
        select(SavedAnalysisSnapshot).where(
            SavedAnalysisSnapshot.address == address,
            SavedAnalysisSnapshot.start_date == payload["start_date"],
            SavedAnalysisSnapshot.end_date == payload["end_date"],
            SavedAnalysisSnapshot.top_n_tokens == top_n_tokens,
        )
    )

    if snapshot is None:
        snapshot = SavedAnalysisSnapshot(
            address=address,
            chain="ethereum",
            start_date=payload["start_date"],
            end_date=payload["end_date"],
            top_n_tokens=top_n_tokens,
        )
        db.add(snapshot)

    nav_curve = payload.get("nav_curve") or []
    snapshot.nav_end_usd = nav_curve[-1]["nav_usd"] if nav_curve else None
    snapshot.total_return = payload.get("metrics", {}).get("total_return")
    snapshot.cagr = payload.get("metrics", {}).get("cagr")
    snapshot.sharpe = payload.get("metrics", {}).get("sharpe")
    snapshot.behavior_style = payload.get("behavior", {}).get("style")
    snapshot.return_driver = payload.get("behavior", {}).get("return_driver")
    snapshot.analysis_source = payload.get("meta", {}).get("source")
    snapshot.payload = _make_json_safe(payload)

    db.commit()
    db.refresh(snapshot)
    return snapshot


def list_saved_analysis_snapshots(
    db: Session,
    limit: int | None = None,
    address: str | None = None,
) -> list[dict[str, Any]]:
    return list_saved_analysis_summaries(db, limit=limit, address=address)


def _build_saved_analysis_summary(snapshot: SavedAnalysisSnapshot) -> dict[str, Any]:
    payload = snapshot.payload or {}
    market_cap_basis = str(payload.get("meta", {}).get("address_market_cap_basis") or "max_nav")
    return {
        "id": snapshot.id,
        "address": snapshot.address,
        "start_date": snapshot.start_date,
        "end_date": snapshot.end_date,
        "top_n_tokens": snapshot.top_n_tokens,
        "market_cap_usd": resolve_market_cap_from_response(payload, basis=market_cap_basis),
        "market_cap_basis": market_cap_basis,
        "nav_end_usd": snapshot.nav_end_usd,
        "total_return": snapshot.total_return,
        "cagr": snapshot.cagr,
        "sharpe": snapshot.sharpe,
        "behavior_style": snapshot.behavior_style,
        "return_driver": snapshot.return_driver,
        "analysis_source": snapshot.analysis_source,
        "saved_at": snapshot.saved_at,
    }


def list_saved_analysis_summaries(
    db: Session,
    limit: int | None = None,
    address: str | None = None,
    sort_by: str = "saved_at",
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    resolved_limit = limit or settings.saved_analysis_limit
    query = select(SavedAnalysisSnapshot).order_by(SavedAnalysisSnapshot.saved_at.desc())
    if address:
        query = (
            select(SavedAnalysisSnapshot)
            .where(SavedAnalysisSnapshot.address == normalize_address(address))
            .order_by(SavedAnalysisSnapshot.saved_at.desc())
        )
    items = [_build_saved_analysis_summary(snapshot) for snapshot in db.scalars(query)]

    allowed_sort_fields = {"saved_at", "total_return", "market_cap_usd"}
    resolved_sort_by = sort_by if sort_by in allowed_sort_fields else "saved_at"
    reverse = sort_order != "asc"
    non_null_items = [item for item in items if item.get(resolved_sort_by) is not None]
    null_items = [item for item in items if item.get(resolved_sort_by) is None]
    non_null_items.sort(key=lambda item: item.get(resolved_sort_by), reverse=reverse)
    return (non_null_items + null_items)[:resolved_limit]


def get_saved_analysis_snapshot(db: Session, snapshot_id: int) -> SavedAnalysisSnapshot | None:
    return db.scalar(
        select(SavedAnalysisSnapshot).where(SavedAnalysisSnapshot.id == snapshot_id)
    )


def get_saved_addresses(db: Session) -> set[str]:
    return {row[0] for row in db.execute(select(SavedAnalysisSnapshot.address)).all()}
