from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import (
    BatchPerformanceResponse,
    BatchRecomputeRequest,
    NetworkRecomputeRequest,
    PerformanceResponse,
    RecomputeRequest,
)
from app.services.network_sync import batch_load_or_sync_address_performance, load_or_sync_address_performance
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
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
