from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import init_db

settings = get_settings()

app = FastAPI(title=settings.app_name)
templates = Jinja2Templates(directory="app/templates")

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup_event() -> None:
    if settings.create_tables_on_startup:
        init_db()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home_page(request: Request) -> HTMLResponse:
    end_date = date.today()
    start_date = end_date - timedelta(days=settings.default_lookback_days)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "default_start_date": start_date.isoformat(),
            "default_end_date": end_date.isoformat(),
            "default_top_n_tokens": 10,
            "default_batch_limit": settings.batch_address_limit,
        },
    )


app.include_router(api_router, prefix=settings.api_prefix)
