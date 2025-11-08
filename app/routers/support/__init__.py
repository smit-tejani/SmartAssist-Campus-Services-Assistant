# app/routers/support/__init__.py
from fastapi import APIRouter
from .appointments import router as appointments_router
from .kb import router as kb_router

router = APIRouter()
router.include_router(appointments_router)
router.include_router(kb_router)
