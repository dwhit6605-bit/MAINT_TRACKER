import os, uuid, mimetypes
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from backend.database import get_db

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_BYTES  = 50 * 1024 * 1024  # 50 MB

router = APIRouter(prefix="/api/task-attachments", tags=["task_attachments"])

ALLOWED_EXTS = {".jpg",".jpeg",".png",".gif",".webp",".heic",".pdf",
                ".doc",".docx",".xls",".xlsx",".csv",".txt",".zip"}


@router.get("/{task_id}")
async def list_attachments(task_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT * FROM task_attachments WHERE task_id=? ORDER BY created_at DESC", (task_id,)
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/{task_id}", status_code=201)
async def upload_attachment(task_id: int, file: UploadFile = File(...), db=Depends(get_db)):
    async with db.execute("SELECT id FROM maintenance_tasks WHERE id=?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Task not found")

    original = os.path.basename(file.filename or "upload")
    ext = os.path.splitext(original)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"File type {ext} not allowed")

    dest_dir = os.path.join(UPLOAD_DIR, "tasks", str(task_id))
    os.makedirs(dest_dir, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(dest_dir, stored)

    size = 0
    with open(dest, "wb") as f:
        while chunk := file.file.read(256 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                f.close(); os.unlink(dest)
                raise HTTPException(413, "File exceeds 50 MB limit")
            f.write(chunk)

    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
    async with db.execute(
        "INSERT INTO task_attachments (task_id,filename,original_name,file_type,file_size) VALUES (?,?,?,?,?)",
        (task_id, stored, original, mime, size)
    ) as cur:
        att_id = cur.lastrowid
    await db.commit()
    return {"id": att_id, "filename": stored, "original_name": original,
            "file_type": mime, "file_size": size}


@router.get("/{task_id}/file/{att_id}")
async def download_attachment(task_id: int, att_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT * FROM task_attachments WHERE id=? AND task_id=?", (att_id, task_id)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Attachment not found")
    path = os.path.join(UPLOAD_DIR, "tasks", str(task_id), row["filename"])
    if not os.path.exists(path):
        raise HTTPException(404, "File missing from disk")
    return FileResponse(path, media_type=row["file_type"],
                        headers={"Content-Disposition": f'inline; filename="{row["original_name"]}"'})


@router.delete("/{task_id}/{att_id}")
async def delete_attachment(task_id: int, att_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT filename FROM task_attachments WHERE id=? AND task_id=?", (att_id, task_id)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Attachment not found")
    path = os.path.join(UPLOAD_DIR, "tasks", str(task_id), row["filename"])
    if os.path.exists(path):
        os.unlink(path)
    await db.execute("DELETE FROM task_attachments WHERE id=?", (att_id,))
    await db.commit()
    return {"ok": True}
