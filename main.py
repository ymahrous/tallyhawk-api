import os
import structlog
import storage_client
import database, models
from sqlmodel import select
from sqlalchemy import delete
from sqlmodel import Session, select
from datetime import datetime, timezone
from tasks import process_document_task
from auth_routes import router as auth_router
from fastapi.middleware.cors import CORSMiddleware
from billing_routes import router as billing_router
from document_routes import router as document_router
from admin.admin_routes import router as admin_router
from feedback_routes import router as feedback_router
from stats.stats_routes import router as stats_router
from vendor.vendor_routes import router as vendor_router
from quickbooks_routes import router as quickbooks_router
from dependencies import get_current_user, increment_usage
from analytics.analytics_routes import router as analytics_router
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ])
logger = structlog.get_logger("tallyhawk.api")

app = FastAPI(title="Tallyhawk API")

allowed_origins_str = os.getenv("ALLOWED_ORIGINS")
origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
    expose_headers=["*"]
)


@app.on_event("startup")
def on_startup():
    database.init_db()
    logger.info("Tallyhawk API started successfully.")

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(stats_router)
app.include_router(vendor_router)
app.include_router(billing_router)
app.include_router(document_router)
app.include_router(feedback_router)
app.include_router(analytics_router)
app.include_router(quickbooks_router)
