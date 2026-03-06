from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AddressCandidate, AddressDailyHolding, TokenDailyPrice
from app.schemas import CandidateIn, HoldingIn, PriceIn, UpsertSummary

router = APIRouter(prefix="/data", tags=["data-ingestion"])


@router.post("/holdings/upsert", response_model=UpsertSummary)
def upsert_holdings(payload: list[HoldingIn], db: Session = Depends(get_db)) -> UpsertSummary:
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload cannot be empty",
        )

    upserted = 0
    for item in payload:
        existing = db.scalar(
            select(AddressDailyHolding).where(
                and_(
                    AddressDailyHolding.address == item.address,
                    AddressDailyHolding.date == item.date,
                    AddressDailyHolding.token_symbol == item.token_symbol,
                )
            )
        )
        if existing is None:
            db.add(
                AddressDailyHolding(
                    address=item.address,
                    chain=item.chain,
                    date=item.date,
                    token_symbol=item.token_symbol,
                    token_contract=item.token_contract,
                    balance=item.balance,
                )
            )
        else:
            existing.chain = item.chain
            existing.token_contract = item.token_contract
            existing.balance = item.balance
        upserted += 1

    db.commit()
    return UpsertSummary(upserted=upserted)


@router.post("/prices/upsert", response_model=UpsertSummary)
def upsert_prices(payload: list[PriceIn], db: Session = Depends(get_db)) -> UpsertSummary:
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload cannot be empty",
        )

    upserted = 0
    for item in payload:
        existing = db.scalar(
            select(TokenDailyPrice).where(
                and_(
                    TokenDailyPrice.token_symbol == item.token_symbol,
                    TokenDailyPrice.date == item.date,
                    TokenDailyPrice.source == item.source,
                )
            )
        )
        if existing is None:
            db.add(
                TokenDailyPrice(
                    token_symbol=item.token_symbol,
                    date=item.date,
                    price_usd=item.price_usd,
                    source=item.source,
                )
            )
        else:
            existing.price_usd = item.price_usd
        upserted += 1

    db.commit()
    return UpsertSummary(upserted=upserted)


@router.post("/candidates/upsert", response_model=UpsertSummary)
def upsert_candidates(payload: list[CandidateIn], db: Session = Depends(get_db)) -> UpsertSummary:
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload cannot be empty",
        )

    upserted = 0
    for item in payload:
        existing = db.scalar(select(AddressCandidate).where(AddressCandidate.address == item.address))
        if existing is None:
            db.add(
                AddressCandidate(
                    address=item.address,
                    chain=item.chain,
                    label=item.label,
                    market_cap_usd=item.market_cap_usd,
                    nav_cagr_90d=item.nav_cagr_90d,
                    nav_volatility_90d=item.nav_volatility_90d,
                    trade_frequency_90d=item.trade_frequency_90d,
                    stable_growth_score=item.stable_growth_score,
                    source=item.source,
                )
            )
        else:
            existing.chain = item.chain
            existing.label = item.label
            existing.market_cap_usd = item.market_cap_usd
            existing.nav_cagr_90d = item.nav_cagr_90d
            existing.nav_volatility_90d = item.nav_volatility_90d
            existing.trade_frequency_90d = item.trade_frequency_90d
            existing.stable_growth_score = item.stable_growth_score
            existing.source = item.source
        upserted += 1

    db.commit()
    return UpsertSummary(upserted=upserted)

