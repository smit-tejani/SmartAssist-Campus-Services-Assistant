from __future__ import annotations

from datetime import datetime

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.oauth import oauth
from app.core.templates import templates
from app.db.mongo import users_collection

router = APIRouter()


@router.post("/register")
async def post_register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    role: str = Form(...),
):
    if password != confirm_password:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Passwords do not match!"},
        )

    if users_collection.find_one({"email": email}):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Email already registered!"},
        )

    users_collection.insert_one(
        {
            "full_name": full_name,
            "email": email,
            "password": password,
            "role": role,
        }
    )
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "message": "Registration successful! Please login."},
    )


@router.post("/login")
async def post_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
):
    user = users_collection.find_one({"email": email})
    if user and user["password"] == password and user["role"] == role:
        request.session["user"] = {
            "full_name": user["full_name"],
            "email": user["email"],
            "role": user["role"],
        }
        if role == "student":
            return RedirectResponse("/student_home", status_code=302)
        if role == "staff":
            return RedirectResponse("/staff_home", status_code=302)
        if role == "admin":
            return RedirectResponse("/admin_home", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Invalid credentials or role!"}
    )


@router.get("/login/google")
async def login_with_google(request: Request):
    return await oauth.google.authorize_redirect(request, settings.google_redirect_uri)


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if user_info:
            user = users_collection.find_one({"email": user_info["email"]})

            if not user:
                users_collection.insert_one(
                    {
                        "full_name": user_info.get("name"),
                        "email": user_info.get("email"),
                        "role": "guest",
                        "created_at": datetime.utcnow(),
                    }
                )

            request.session["user"] = {
                "full_name": user_info.get("name"),
                "email": user_info.get("email"),
                "role": user.get("role", "guest") if user else "guest",
            }

            return RedirectResponse(url="/guest_home")

        return RedirectResponse(url="/login")

    except Exception as exc:
        print(f"[ERROR] OAuth callback failed: {exc}")
        code = request.query_params.get("code")
        if not code:
            return RedirectResponse(url="/login")

        token_url = "https://oauth2.googleapis.com/token"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if response.status_code != 200:
                return RedirectResponse(url="/login")

            token_data = response.json()
            access_token = token_data.get("access_token")
            userinfo_response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_response.status_code != 200:
                return RedirectResponse(url="/login")

            user_info = userinfo_response.json()
            user = users_collection.find_one({"email": user_info.get("email")})
            if not user:
                users_collection.insert_one(
                    {
                        "full_name": user_info.get("name"),
                        "email": user_info.get("email"),
                        "role": "guest",
                        "created_at": datetime.utcnow(),
                    }
                )
            request.session["user"] = {
                "full_name": user_info.get("name"),
                "email": user_info.get("email"),
                "role": user.get("role", "guest") if user else "guest",
            }
            return RedirectResponse(url="/guest_home")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")
