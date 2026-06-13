import os
import bcrypt
from datetime import datetime, timedelta
from fastapi import HTTPException, Request
from jose import JWTError, jwt

SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-CHANGE-IN-PRODUCTION")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def require_tech(request: Request):
    """Allows admin and tech roles. Operators get 403."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") not in ("admin", "tech"):
        raise HTTPException(403, "Tech access required")
    return user
