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


def _qr_png(url: str) -> Response:
    """Shared renderer. Error-correct M survives a scuffed shelf label."""
    qr = qrcode.QRCode(version=1, box_size=8, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@router.get("/location/{loc_id}")
async def get_location_qr(loc_id: int, db=Depends(get_db)):
    """Shelf/zone label — scanning gives a roll-up of what should be there."""
    async with db.execute(
        "SELECT id, code FROM inventory_locations WHERE id=?", (loc_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Location not found")
    return _qr_png(f"{BASE_URL}/inventory?loc={loc_id}")


@router.get("/inventory/{item_id}")
async def get_inventory_qr(item_id: int, loc: int | None = None, db=Depends(get_db)):
    """Item label. With ?loc it is a per-shelf label and the scan knows the bin,
    so the quick-adjust panel can act without asking which location you mean."""
    async with db.execute("SELECT id FROM inventory_items WHERE id=?", (item_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Item not found")
    if loc is not None:
        async with db.execute(
            "SELECT id FROM inventory_locations WHERE id=?", (loc,)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Location not found")
        return _qr_png(f"{BASE_URL}/inventory?item={item_id}&loc={loc}")
    return _qr_png(f"{BASE_URL}/inventory?item={item_id}")
