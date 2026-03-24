from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import BatchJob, BatchJobResult
from app.services.network_sync import load_or_sync_address_performance
from app.services.performance import resolve_market_cap_from_response



def create_batch_job(
    db: Session,
    addresses: list[str],
    start_date: date,
    end_date: date,
    top_n_tokens: int,
    market_cap_basis: str,
    refresh: bool,
) -> BatchJob:
    job = BatchJob(
        addresses=addresses,
        start_date=start_date,
        end_date=end_date,
        top_n_tokens=top_n_tokens,
        market_cap_basis=market_cap_basis,
        refresh=refresh,
        total_addresses=len(addresses),
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _summarize_payload(payload: dict[str, Any], market_cap_basis: str) -> dict[str, Any]:
    nav_curve = payload.get("nav_curve") or []
    metrics = payload.get("metrics") or {}
    behavior = payload.get("behavior") or {}
    meta = payload.get("meta") or {}
    return {
        "market_cap_usd": resolve_market_cap_from_response(payload, basis=market_cap_basis),
        "market_cap_basis": market_cap_basis,
        "nav_end_usd": nav_curve[-1]["nav_usd"] if nav_curve else None,
        "total_return": metrics.get("total_return"),
        "cagr": metrics.get("cagr"),
        "sharpe": metrics.get("sharpe"),
        "behavior_style": behavior.get("style"),
        "return_driver": behavior.get("return_driver"),
        "explanation": behavior.get("summary"),
        "used_cache": meta.get("used_cache"),
        "runtime_seconds": meta.get("runtime_seconds"),
    }


def _save_batch_result(
    session: Session,
    job: BatchJob,
    address: str,
    success: bool,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    result_data: dict[str, Any] = {
        "batch_job_id": job.id,
        "address": address,
        "success": success,
        "market_cap_basis": job.market_cap_basis,
        "processed_at": datetime.now(timezone.utc),
        "payload": payload,
    }

    if success and payload:
        result_data.update(_summarize_payload(payload, job.market_cap_basis))
    if not success:
        result_data["error"] = error

    result = BatchJobResult(**result_data)
    session.add(result)

    if success:
        job.completed += 1
    else:
        job.failed += 1

    session.commit()


def process_batch_job(job_id: int) -> None:
    with SessionLocal() as session:
        job = session.get(BatchJob, job_id)
        if job is None:
            return
        if job.status not in {"pending", "failed"}:
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        job.completed = 0
        job.failed = 0
        job.fault_text = None
        session.commit()

        addresses = job.addresses or []
        for address in addresses:
            try:
                payload = load_or_sync_address_performance(
                    session,
                    raw_address=address,
                    start_date=job.start_date,
                    end_date=job.end_date,
                    top_n_tokens=job.top_n_tokens,
                    refresh=job.refresh,
                )
                _save_batch_result(session, job, address, success=True, payload=payload)
            except ValueError as error:
                _save_batch_result(session, job, address, success=False, error=str(error))
            except Exception as error:  # pragma: no cover - defensive guard
                job.status = "failed"
                job.fault_text = str(error)
                job.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()


def _map_result_to_item(result: BatchJobResult) -> dict[str, Any]:
    return {
        "address": result.address,
        "success": result.success,
        "market_cap_usd": result.market_cap_usd,
        "market_cap_basis": result.market_cap_basis,
        "nav_end_usd": result.nav_end_usd,
        "total_return": result.total_return,
        "cagr": result.cagr,
        "sharpe": result.sharpe,
        "behavior_style": result.behavior_style,
        "return_driver": result.return_driver,
        "explanation": result.explanation,
        "used_cache": result.used_cache,
        "runtime_seconds": result.runtime_seconds,
        "error": result.error,
    }


def get_batch_job_with_results(db: Session, job_id: int) -> tuple[BatchJob, list[dict[str, Any]]] | None:
    job = db.get(BatchJob, job_id)
    if job is None:
        return None

    results = list(
        db.scalars(
            select(BatchJobResult)
            .where(BatchJobResult.batch_job_id == job_id)
            .order_by(BatchJobResult.id.asc())
        )
    )
    return job, [_map_result_to_item(result) for result in results]
