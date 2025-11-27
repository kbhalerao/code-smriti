"""Minimal user routes (stub)"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def get_user():
    """Get user info (stub)"""
    return {"message": "User routes - placeholder"}
