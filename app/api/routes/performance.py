from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import (
    BatchPerformanceResponse,
    BatchRecomputeRequest,
    NetworkRecomputeRequest,
    PerformanceResponse,
    RandomAddressesResponse,
    RecomputeRequest,
    SavedAnalysisListResponse,
)
from app.services.analysis_store import (
    get_saved_analysis_snapshot,
    hydrate_saved_performance_payload,
    list_saved_analysis_summaries,
)
from app.services.network_sync import (
    batch_load_or_sync_address_performance,
    generate_random_recent_addresses,
    load_or_sync_address_performance,
)
from app.services.performance import get_cached_address_performance, recompute_address_performance

router = APIRouter(prefix="/performance", tags=["performance"])


@router.post("/{address}/recompute", response_model=PerformanceResponse)
def recompute_performance(
    address: str,
    payload: RecomputeRequest,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return recompute_address_performance(
            db,
            raw_address=address,
            start_date=payload.start_date,
            end_date=payload.end_date,
            top_n_tokens=payload.top_n_tokens,
        )
    except ValueError as error:
        detail = str(error)
        status_code = status.HTTP_404_NOT_FOUND if "no" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from error


@router.post("/batch/network/recompute", response_model=BatchPerformanceResponse)
def recompute_batch_from_network(
    payload: BatchRecomputeRequest,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return batch_load_or_sync_address_performance(
            db,
            addresses=payload.addresses,
            start_date=payload.start_date,
            end_date=payload.end_date,
            top_n_tokens=payload.top_n_tokens,
            refresh=payload.refresh,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/random-addresses", response_model=RandomAddressesResponse)
def get_random_addresses(
    count: int = Query(default=50, ge=1, le=50),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    top_n_tokens: int = Query(default=10, ge=1, le=100),
    min_market_cap_usd: float = Query(default=0, ge=0),
    market_cap_basis: str = Query(default="max_nav"),
    exclude_saved: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    resolved_end_date = end_date or date.today()
    resolved_start_date = start_date or (resolved_end_date - timedelta(days=90))
    if resolved_end_date < resolved_start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be greater than or equal to start_date",
        )

    try:
        addresses = generate_random_recent_addresses(
            db,
            count=count,
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            top_n_tokens=top_n_tokens,
            min_market_cap_usd=min_market_cap_usd,
            market_cap_basis=market_cap_basis,
            exclude_saved=exclude_saved,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {
        "count": len(addresses),
        "addresses": addresses,
        "source": "etherscan-recent-block-senders",
        "min_market_cap_usd": min_market_cap_usd,
        "market_cap_basis": market_cap_basis,
    }


@router.get("/saved", response_model=SavedAnalysisListResponse)
def list_saved_analyses(
    limit: int = Query(default=100, ge=1, le=200),
    address: str | None = Query(default=None),
    sort_by: str = Query(default="saved_at"),
    sort_order: str = Query(default="desc"),
    db: Session = Depends(get_db),
) -> dict:
    items = list_saved_analysis_summaries(
        db,
        limit=limit,
        address=address,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return {"items": items}


@router.get("/saved/{snapshot_id}", response_model=PerformanceResponse)
def get_saved_analysis(
    snapshot_id: int,
    db: Session = Depends(get_db),
) -> dict:
    snapshot = get_saved_analysis_snapshot(db, snapshot_id)
    if snapshot is None or snapshot.payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="saved analysis not found")
    return hydrate_saved_performance_payload(snapshot.payload)


@router.post("/{address}/network/recompute", response_model=PerformanceResponse)
def recompute_from_network(
    address: str,
    payload: NetworkRecomputeRequest,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return load_or_sync_address_performance(
            db,
            raw_address=address,
            start_date=payload.start_date,
            end_date=payload.end_date,
            top_n_tokens=payload.top_n_tokens,
            refresh=payload.refresh,
        )
    except ValueError as error:
        detail = str(error)
        status_code = status.HTTP_404_NOT_FOUND if "no" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from error


@router.get("/{address}", response_model=PerformanceResponse)
def get_performance(
    address: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
    top_n_tokens: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be greater than or equal to start_date",
        )

    try:
        return get_cached_address_performance(
            db,
            raw_address=address,
            start_date=start_date,
            end_date=end_date,
            top_n_tokens=top_n_tokens,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
