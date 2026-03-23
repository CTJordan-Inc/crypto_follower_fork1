from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AddressCandidate, WatchlistAddress
from app.schemas import AddressRecommendation, WatchlistCreate, WatchlistRead

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.post("", response_model=WatchlistRead)
def upsert_watchlist_address(payload: WatchlistCreate, db: Session = Depends(get_db)) -> WatchlistAddress:
    existing = db.scalar(select(WatchlistAddress).where(WatchlistAddress.address == payload.address))

    if existing is None:
        existing = WatchlistAddress(
            address=payload.address,
            chain=payload.chain,
            label=payload.label,
            label_source=payload.label_source,
            is_exchange=payload.is_exchange,
            is_contract=payload.is_contract,
        )
        db.add(existing)
    else:
        existing.chain = payload.chain
        existing.label = payload.label
        existing.label_source = payload.label_source
        existing.is_exchange = payload.is_exchange
        existing.is_contract = payload.is_contract

    db.commit()
    db.refresh(existing)
    return existing


@router.get("", response_model=list[WatchlistRead])
def list_watchlist(db: Session = Depends(get_db)) -> list[WatchlistAddress]:
    return list(
        db.scalars(select(WatchlistAddress).order_by(WatchlistAddress.created_at.desc()))
    )


@router.get("/recommendations", response_model=list[AddressRecommendation])
def list_recommendations(
    min_market_cap: float = Query(default=10_000_000, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[AddressCandidate]:
    query = (
        select(AddressCandidate)
        .where(AddressCandidate.market_cap_usd >= min_market_cap)
        .order_by(AddressCandidate.stable_growth_score.desc(), AddressCandidate.market_cap_usd.desc())
        .limit(limit)
    )
    return list(db.scalars(query))

