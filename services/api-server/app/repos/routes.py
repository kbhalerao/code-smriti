"""Minimal repos routes (stub)"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_repos():
    """List repos (stub)"""
    return {"message": "Repos routes - placeholder"}
