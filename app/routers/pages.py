from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core.templates import templates
from app.dependencies.auth import get_current_user, role_required

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
async def get_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/student_home", response_class=HTMLResponse)
async def student_dashboard(request: Request, user: dict = Depends(role_required("student"))):
    return templates.TemplateResponse("student_home.html", {"request": request})


@router.get("/staff_home", response_class=HTMLResponse)
async def staff_dashboard(request: Request, user: dict = Depends(role_required("staff"))):
    return templates.TemplateResponse("staff_home.html", {"request": request, "user": user})


@router.get("/admin_home", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user: dict = Depends(role_required("admin"))):
    return templates.TemplateResponse("admin_home.html", {"request": request})


@router.get("/knowledge_base", response_class=HTMLResponse)
async def knowledge_base(request: Request, user: dict = Depends(role_required("admin"))):
    return templates.TemplateResponse("knowledge_base.html", {"request": request})


@router.get("/edit_profile", response_class=HTMLResponse)
async def edit_profile(request: Request, user: dict = Depends(role_required("student"))):
    return templates.TemplateResponse("edit_profile.html", {"request": request})


@router.get("/guest_home", response_class=HTMLResponse)
async def guest_dashboard(request: Request, user: dict = Depends(role_required("guest"))):
    return templates.TemplateResponse("guest_home.html", {"request": request})


@router.get("/contact_support", response_class=HTMLResponse)
async def contact_support(request: Request):
    return templates.TemplateResponse("contact_support.html", {"request": request})


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["guest", "student", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    return templates.TemplateResponse("chat.html", {"request": request})
