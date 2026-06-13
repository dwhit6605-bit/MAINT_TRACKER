"""Generate a PMCS completion archive PDF."""
import io
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors

W, H = letter
M = 0.5 * inch

STATUS_LABEL = {"ok": "OK", "fault": "FAULT", "na": "N/A"}
STATUS_COLOR = {
    "ok":    colors.HexColor("#15803d"),
    "fault": colors.HexColor("#dc2626"),
    "na":    colors.HexColor("#6b7280"),
}
INTERVAL_COLORS = {
    "B": colors.HexColor("#1d4ed8"),
    "D": colors.HexColor("#0369a1"),
    "A": colors.HexColor("#0f766e"),
    "W": colors.HexColor("#7c3aed"),
    "M": colors.HexColor("#b45309"),
}


def _wrap_text(c, x, y, text, font, size, max_w, line_height=None):
    if not text:
        return y
    lh = line_height or size + 2
    c.setFont(font, size)
    words = str(text).split()
    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        if c.stringWidth(test, font, size) <= max_w:
            line.append(w)
        else:
            if line:
                lines.append(" ".join(line))
            line = [w]
    if line:
        lines.append(" ".join(line))
    for ln in lines:
        c.drawString(x, y, ln)
        y -= lh
    return y


def generate_pmcs_pdf(template_title: str, equipment_name: str,
                      operator_name: str, operator_rank: str,
                      completed_at: str, session_notes: str,
                      items_results: list) -> bytes:
    """
    items_results: list of dicts with keys:
      item_no, interval, check_item, procedure, not_ready_if, status, notes
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setLineWidth(0.5)

    fault_items = [r for r in items_results if r.get("status") == "fault"]
    total = len(items_results)
    faults = len(fault_items)

    def new_page():
        c.showPage()
        c.setLineWidth(0.5)
        return H - M

    # ── Header ────────────────────────────────────────────────────────────────
    y = H - M

    # Title bar
    c.setFillColor(colors.HexColor("#1e3a5f"))
    c.rect(M, y - 36, W - 2*M, 36, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(M + 10, y - 22, "PMCS COMPLETION RECORD")
    c.setFont("Helvetica", 8)
    c.drawRightString(W - M - 10, y - 14, f"Generated: {completed_at[:16].replace('T',' ')}")
    c.drawRightString(W - M - 10, y - 24, "GEAR GUARD · maint.whitwerx.net")
    y -= 36

    # Info grid
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(M, y - 54, W - 2*M, 54, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.rect(M, y - 54, W - 2*M, 54, fill=0, stroke=1)
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 7)
    col1x, col2x, col3x = M + 8, M + (W - 2*M)*0.38, M + (W - 2*M)*0.68
    c.drawString(col1x, y - 12, "CHECKLIST")
    c.drawString(col2x, y - 12, "OPERATOR")
    c.drawString(col3x, y - 12, "RESULT")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(col1x, y - 24, template_title[:45])
    c.setFont("Helvetica", 8)
    c.drawString(col1x, y - 35, equipment_name[:45] if equipment_name else "—")
    c.drawString(col1x, y - 46, f"Date: {completed_at[:10]}")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(col2x, y - 24, operator_name or "—")
    c.setFont("Helvetica", 8)
    c.drawString(col2x, y - 35, operator_rank or "")

    # Result badge
    ok_all = faults == 0
    badge_color = colors.HexColor("#15803d") if ok_all else colors.HexColor("#dc2626")
    c.setFillColor(badge_color)
    c.roundRect(col3x, y - 46, 90, 30, 4, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    label = "ALL CLEAR" if ok_all else f"{faults} FAULT{'S' if faults>1 else ''}"
    c.drawCentredString(col3x + 45, y - 28, label)
    c.setFont("Helvetica", 7)
    c.drawCentredString(col3x + 45, y - 39, f"{total - faults}/{total} items OK")
    y -= 54

    # ── Fault summary (if any) ────────────────────────────────────────────────
    if fault_items:
        y -= 8
        c.setFillColor(colors.HexColor("#fef2f2"))
        c.setStrokeColor(colors.HexColor("#fca5a5"))
        box_h = 16 + len(fault_items) * 14
        c.rect(M, y - box_h, W - 2*M, box_h, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#dc2626"))
        c.setFont("Helvetica-Bold", 8)
        c.drawString(M + 8, y - 12, f"FAULTS FOUND ({faults})")
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 8)
        for i, fi in enumerate(fault_items):
            fx = M + 8
            fy = y - 24 - i * 14
            c.setFillColor(colors.HexColor("#dc2626"))
            c.drawString(fx, fy, f"#{fi.get('item_no','?')} [{fi.get('interval','?')}]")
            c.setFillColor(colors.black)
            note = fi.get("notes") or ""
            desc = fi.get("check_item","")
            summary = f"{desc[:60]}{'...' if len(desc)>60 else ''}"
            if note:
                summary += f"  — {note[:40]}"
            c.drawString(fx + 60, fy, summary[:90])
        y -= box_h + 8

    # ── Column headers ────────────────────────────────────────────────────────
    y -= 4
    COL = {"no": 0.04, "int": 0.05, "item": 0.38, "proc": 0.25, "result": 0.10, "notes": 0.18}
    col_w = {k: (W - 2*M) * v for k, v in COL.items()}
    headers = [("NO", "no"), ("INT", "int"), ("CHECK ITEM", "item"),
               ("PROCEDURE", "proc"), ("RESULT", "result"), ("NOTES", "notes")]
    c.setFillColor(colors.HexColor("#334155"))
    c.rect(M, y - 16, W - 2*M, 16, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7)
    hx = M
    for label, key in headers:
        c.drawCentredString(hx + col_w[key]/2, y - 11, label)
        hx += col_w[key]
    y -= 16

    # ── Item rows ─────────────────────────────────────────────────────────────
    ROW_MIN = 22
    for idx, r in enumerate(items_results):
        # estimate row height
        item_lines = max(1, len(r.get("check_item","")) // 35 + 1)
        proc_lines = max(1, len(r.get("procedure","")) // 28 + 1) if r.get("procedure") else 1
        note_lines = max(1, len(r.get("notes","")) // 20 + 1) if r.get("notes") else 1
        row_h = max(ROW_MIN, max(item_lines, proc_lines, note_lines) * 10 + 8)

        if y - row_h < M + 20:
            y = new_page()
            y -= M

        bg = colors.HexColor("#f8fafc") if idx % 2 == 0 else colors.white
        c.setFillColor(bg)
        c.rect(M, y - row_h, W - 2*M, row_h, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#e2e8f0"))
        c.line(M, y - row_h, W - M, y - row_h)

        tx = M
        ty = y - 10

        # item_no
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 7)
        c.drawCentredString(tx + col_w["no"]/2, ty, r.get("item_no",""))
        tx += col_w["no"]

        # interval badge
        intv = r.get("interval", "B")
        ic = INTERVAL_COLORS.get(intv, colors.gray)
        c.setFillColor(ic)
        c.roundRect(tx + 2, ty - 3, col_w["int"] - 4, 12, 2, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(tx + col_w["int"]/2, ty, intv)
        tx += col_w["int"]

        # check item
        c.setFillColor(colors.black)
        _wrap_text(c, tx + 2, ty, r.get("check_item",""), "Helvetica", 7.5,
                   col_w["item"] - 4, line_height=10)
        tx += col_w["item"]

        # procedure
        _wrap_text(c, tx + 2, ty, r.get("procedure",""), "Helvetica", 7,
                   col_w["proc"] - 4, line_height=9)
        tx += col_w["proc"]

        # result
        st = r.get("status", "ok")
        sc = STATUS_COLOR.get(st, colors.gray)
        c.setFillColor(sc)
        c.roundRect(tx + 3, ty - 4, col_w["result"] - 6, 14, 3, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawCentredString(tx + col_w["result"]/2, ty, STATUS_LABEL.get(st, st.upper()))
        tx += col_w["result"]

        # notes
        c.setFillColor(colors.black)
        _wrap_text(c, tx + 2, ty, r.get("notes",""), "Helvetica", 7,
                   col_w["notes"] - 4, line_height=9)

        y -= row_h

    # ── Signature block ───────────────────────────────────────────────────────
    if y < M + 60:
        y = new_page()
        y -= M
    y -= 10
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(M, y - 48, W - 2*M, 48, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 7)
    sig_col = (W - 2*M) / 3
    c.drawString(M + 8, y - 12, "OPERATOR SIGNATURE")
    c.drawString(M + sig_col + 8, y - 12, "RANK / NAME")
    c.drawString(M + sig_col*2 + 8, y - 12, "DATE / TIME")
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.line(M + 8, y - 32, M + sig_col - 8, y - 32)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawString(M + sig_col + 8, y - 30,
                 f"{operator_rank or ''} {operator_name or ''}".strip() or "—")
    c.drawString(M + sig_col*2 + 8, y - 30, completed_at[:16].replace("T", " "))
    if session_notes:
        c.setFont("Helvetica-Oblique", 7)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(M + 8, y - 44, f"Notes: {session_notes[:120]}")

    # ── Footer ────────────────────────────────────────────────────────────────
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#94a3b8"))
    c.drawString(M, M - 4, "PMCS Completion Record — GEAR GUARD")
    c.drawRightString(W - M, M - 4, completed_at[:10])

    c.save()
    return buf.getvalue()
