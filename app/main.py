"""
FastAPI application for Russian tax declaration system (ИП на УСН 6%).
"""

import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import init_db
from app.routers import projects, import_data, operations, tax, export, audit, wizard


# Paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on application startup."""
    init_db()
    yield


app = FastAPI(
    title="Налоговая декларация УСН 6%",
    description="Система расчёта налога для ИП на УСН 6%",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include API routers FIRST (before static mount)
app.include_router(projects.router)
app.include_router(import_data.router)
app.include_router(operations.router)
app.include_router(tax.router)
app.include_router(export.router)
app.include_router(audit.router)
app.include_router(wizard.router)


# Root — serve index.html
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Serve the main page."""
    if INDEX_HTML.exists():
        return HTMLResponse(content=INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Налоговая декларация УСН 6%</h1><p>index.html не найден</p>")


# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Система готова к работе"}


# Mount static files LAST so it doesn't intercept API and root routes
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
