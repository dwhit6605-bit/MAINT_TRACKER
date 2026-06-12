import io
import os
import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from backend.database import get_db

router = APIRouter(prefix="/api/qr", tags=["qr"])

BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")


@router.get("/pmcs/{template_id}")
async def get_pmcs_qr(template_id: int, db=Depends(get_db)):
    async with db.execute("SELECT id, title FROM pmcs_templates WHERE id=?",
                          (template_id,)) as cur:
        tmpl = await cur.fetchone()
    if not tmpl:
        raise HTTPException(404, "Template not found")

    url = f"{BASE_URL}/pmcs/{template_id}"
    qr = qrcode.QRCode(version=1, box_size=8, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@router.get("/equipment/{equipment_id}")
async def get_equipment_qr(equipment_id: int, db=Depends(get_db)):
    async with db.execute("SELECT id, name, serial_num FROM equipment WHERE id=?",
                          (equipment_id,)) as cur:
        eq = await cur.fetchone()
    if not eq:
        raise HTTPException(404, "Equipment not found")

    url = f"{BASE_URL}/equipment?open={equipment_id}"
    qr = qrcode.QRCode(version=1, box_size=8, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})
