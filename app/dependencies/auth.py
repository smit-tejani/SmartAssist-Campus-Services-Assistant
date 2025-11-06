from __future__ import annotations

from fastapi import Depends, HTTPException, Request


def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not user.get("role") or not user.get("email"):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def role_required(required_role: str):
    def dependency(user: dict = Depends(get_current_user)):
        if user.get("role") != required_role:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return dependency
__all__ = ["get_current_user", "role_required"]
