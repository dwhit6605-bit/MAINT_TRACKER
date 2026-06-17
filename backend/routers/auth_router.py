from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import hash_password, verify_password, create_token, require_admin, require_superadmin, VALID_ROLES

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "operator"


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None


@router.post("/login")
async def login(data: LoginRequest, db=Depends(get_db)):
    async with db.execute("SELECT * FROM users WHERE username=?", (data.username,)) as cur:
        user = await cur.fetchone()
    if not user or not verify_password(data.password, user["hashed_password"]):
        raise HTTPException(401, "Invalid username or password")
    await db.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
    await db.commit()
    token = create_token(user["id"], user["username"], user["role"])
    return {"token": token, "username": user["username"], "role": user["role"]}


@router.get("/me")
async def me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@router.get("/users")
async def list_users(request: Request, db=Depends(get_db)):
    require_admin(request)
    async with db.execute(
        "SELECT id, username, role, created_at, last_login FROM users ORDER BY username"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/users", status_code=201)
async def create_user(data: UserCreate, request: Request, db=Depends(get_db)):
    require_admin(request)
    if data.role not in VALID_ROLES:
        raise HTTPException(400, f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")
    async with db.execute("SELECT id FROM users WHERE username=?", (data.username,)) as cur:
        if await cur.fetchone():
            raise HTTPException(409, "Username already exists")
    async with db.execute(
        "INSERT INTO users (username, hashed_password, role) VALUES (?,?,?)",
        (data.username, hash_password(data.password), data.role),
    ) as cur:
        uid = cur.lastrowid
    await db.commit()
    return {"id": uid}


@router.put("/users/{uid}")
async def update_user(uid: int, data: UserUpdate, request: Request, db=Depends(get_db)):
    current = getattr(request.state, "user", {}) or {}
    if current.get("sub") != str(uid):
        require_admin(request)
    if data.role and current.get("role") not in ("admin", "team_chief"):
        raise HTTPException(403, "Only admins or team chiefs can change roles")
    if data.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role")

    updates, vals = [], []
    if data.username:
        updates.append("username=?"); vals.append(data.username)
    if data.password:
        updates.append("hashed_password=?"); vals.append(hash_password(data.password))
    if data.role:
        updates.append("role=?"); vals.append(data.role)
    if not updates:
        return {"ok": True}
    vals.append(uid)
    await db.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", vals)
    await db.commit()
    return {"ok": True}


@router.delete("/users/{uid}")
async def delete_user(uid: int, request: Request, db=Depends(get_db)):
    require_superadmin(request)
    current = getattr(request.state, "user", {}) or {}
    if current.get("sub") == str(uid):
        raise HTTPException(400, "Cannot delete yourself")
    async with db.execute("SELECT role FROM users WHERE id=?", (uid,)) as cur:
        target = await cur.fetchone()
    if target and target["role"] == "admin":
        async with db.execute("SELECT COUNT(*) as c FROM users WHERE role='admin'") as cur:
            cnt = (await cur.fetchone())["c"]
        if cnt <= 1:
            raise HTTPException(400, "Cannot delete the last admin")
    await db.execute("DELETE FROM users WHERE id=?", (uid,))
    await db.commit()
    return {"ok": True}
