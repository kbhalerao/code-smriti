"""Minimal jobs routes (stub)"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_jobs():
    """List jobs (stub)"""
    return {"message": "Jobs routes - placeholder"}
