import os, shutil, mimetypes
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from backend.database import get_db

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_BYTES  = 50 * 1024 * 1024  # 50 MB

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("/{eq_id}")
async def list_attachments(eq_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT * FROM equipment_attachments WHERE equipment_id=? ORDER BY created_at DESC
    """, (eq_id,)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/{eq_id}", status_code=201)
async def upload_attachment(eq_id: int, file: UploadFile = File(...), db=Depends(get_db)):
    # verify equipment exists
    async with db.execute("SELECT id FROM equipment WHERE id=?", (eq_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Equipment not found")

    dest_dir = os.path.join(UPLOAD_DIR, "equipment", str(eq_id))
    os.makedirs(dest_dir, exist_ok=True)

    # safe filename: strip path chars, keep extension
    original = os.path.basename(file.filename or "upload")
    ext = os.path.splitext(original)[1].lower()
    allowed_exts = {".jpg",".jpeg",".png",".gif",".webp",".pdf",".doc",".docx",
                    ".xls",".xlsx",".csv",".txt",".zip"}
    if ext not in allowed_exts:
        raise HTTPException(400, f"File type {ext} not allowed")

    # unique stored filename
    import uuid
    stored = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(dest_dir, stored)

    size = 0
    with open(dest, "wb") as f:
        while chunk := file.file.read(1024 * 256):
            size += len(chunk)
            if size > MAX_BYTES:
                f.close()
                os.unlink(dest)
                raise HTTPException(413, "File exceeds 50 MB limit")
            f.write(chunk)

    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"

    async with db.execute("""
        INSERT INTO equipment_attachments (equipment_id, filename, original_name, file_type, file_size)
        VALUES (?,?,?,?,?)
    """, (eq_id, stored, original, mime, size)) as cur:
        att_id = cur.lastrowid
    await db.commit()
    return {"id": att_id, "filename": stored, "original_name": original, "file_type": mime, "file_size": size}


@router.get("/{eq_id}/file/{att_id}")
async def download_attachment(eq_id: int, att_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT * FROM equipment_attachments WHERE id=? AND equipment_id=?
    """, (att_id, eq_id)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Attachment not found")
    path = os.path.join(UPLOAD_DIR, "equipment", str(eq_id), row["filename"])
    if not os.path.exists(path):
        raise HTTPException(404, "File missing from disk")
    return FileResponse(path, media_type=row["file_type"],
                        headers={"Content-Disposition": f'inline; filename="{row["original_name"]}"'})


@router.delete("/{eq_id}/{att_id}")
async def delete_attachment(eq_id: int, att_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT filename FROM equipment_attachments WHERE id=? AND equipment_id=?
    """, (att_id, eq_id)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Attachment not found")
    path = os.path.join(UPLOAD_DIR, "equipment", str(eq_id), row["filename"])
    if os.path.exists(path):
        os.unlink(path)
    await db.execute("DELETE FROM equipment_attachments WHERE id=?", (att_id,))
    await db.commit()
    return {"ok": True}
