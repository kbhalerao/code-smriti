"""Minimal search routes (stub)"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def search():
    """Search (stub)"""
    return {"message": "Search routes - placeholder"}
