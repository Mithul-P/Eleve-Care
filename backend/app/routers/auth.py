from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..db import clean, utcnow
from ..deps import db_dep
from ..security import AdminUser, CurrentUser, create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)


class NewUser(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    name: str = Field(..., min_length=1, max_length=80)
    role: Literal["admin", "staff"] = "staff"
    password: str = Field(..., min_length=8, max_length=256)


def _public(user: dict) -> dict:
    return {k: v for k, v in clean(user).items() if k != "password_hash"}


@router.post("/login")
async def login(body: LoginRequest, db=Depends(db_dep)):
    user = await db.users.find_one({"email": body.email.strip().lower()})
    if not user or not user.get("active", True) or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": utcnow()}})
    return {"access_token": create_token(user), "token_type": "bearer", "user": _public(user)}


@router.get("/me")
async def me(user: CurrentUser):
    return _public(user)


@router.get("/users")
async def list_users(_: AdminUser, db=Depends(db_dep)):
    return [_public(u) async for u in db.users.find({}, {"password_hash": 0})]


@router.post("/users", status_code=201)
async def create_user(body: NewUser, _: AdminUser, db=Depends(db_dep)):
    email = body.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "A user with this email already exists")
    user = {"_id": f"usr-{secrets.token_hex(5)}", "email": email, "name": body.name, "role": body.role,
            "password_hash": hash_password(body.password), "active": True, "created_at": utcnow()}
    await db.users.insert_one(user)
    return _public(user)
