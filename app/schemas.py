from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class AuthRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class AuthResponse(BaseModel):
    token: str
    email: EmailStr


class PositionIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    name: str
    weight: float = Field(ge=0, le=1)
    cost_price: float | None = Field(default=None, ge=0)
    shares: float | None = Field(default=None, ge=0)


class PositionOut(PositionIn):
    id: int


class WatchlistIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    name: str
    note: str | None = None


class WatchlistOut(WatchlistIn):
    id: int
