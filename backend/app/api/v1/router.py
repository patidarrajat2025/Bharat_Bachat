"""Central API router preserving the existing route modules and URL contracts."""
from fastapi import APIRouter
from app.api import auth, group, reports, super_admin

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(super_admin.router)
api_router.include_router(group.router)
api_router.include_router(reports.router)
