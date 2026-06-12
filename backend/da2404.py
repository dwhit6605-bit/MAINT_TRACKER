"""
Generate DA Form 2404 — Equipment Inspection and Maintenance Worksheet.
Produces a filled, print-ready PDF from task completion data.
"""
import io
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors

W, H = letter   # 612 x 792 pt


# ── helpers ───────────────────────────────────────────────────────────────────
def _box(c, x, y, w, h):
    c.rect(x, y, w, h)

def _label(c, x, y, text, size=6, bold=False):
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, text)

def _value(c, x, y, text, size=9, max_width=None):
    """Draw a field value, truncating if needed."""
    if not text:
        return
    c.setFont("Helvetica", size)
    if max_width:
        # wrap long text
        words = str(text).split()
        lines, line = [], []
        for w in words:
            test = " ".join(line + [w])
            if c.stringWidth(test, "Helvetica", size) <= max_width:
                line.append(w)
            else:
                if line:
                    lines.append(" ".join(line))
                line = [w]
        if line:
            lines.append(" ".join(line))
        for i, ln in enumerate(lines[:3]):
            c.drawString(x, y - i * (size + 2), ln)
    else:
        c.drawString(x, y, str(text)[:120])

def _wrap(c, x, y, text, size, max_width, line_height=None):
    if not text:
        return y
    lh = line_height or (size + 2)
    c.setFont("Helvetica", size)
    words = str(text).split()
    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        if c.stringWidth(test, "Helvetica", size) <= max_width:
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


# ── main generator ────────────────────────────────────────────────────────────
def generate_da2404(
    # equipment
    organization: str = "",
    nomenclature: str = "",
    serial_nsn: str = "",
    # task
    inspection_date: str = "",
    inspection_type: str = "Scheduled",
    tm_number: str = "",
    tm_date: str = "",
    # deficiencies / corrective actions  (list of dicts: item_no, status, deficiency, corrective_action)
    line_items: list = None,
    # signatures
    inspector_name: str = "",
    inspector_time: str = "",
    supervisor_name: str = "",
    supervisor_time: str = "",
    manhours: str = "",
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setLineWidth(0.5)

    _draw_page1(c, organization, nomenclature, serial_nsn,
                inspection_date, inspection_type, tm_number, tm_date,
                line_items or [], inspector_name, inspector_time,
                supervisor_name, supervisor_time, manhours)

    c.save()
    return buf.getvalue()


def _draw_page1(c, org, nom, serial, date, insp_type, tm_num, tm_date,
                items, inspector, insp_time, supervisor, sup_time, manhours):
    M = 0.4 * inch          # margin
    FW = W - 2 * M          # form width
    y = H - M               # top cursor

    # ── Title ─────────────────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(W / 2, y - 14, "EQUIPMENT INSPECTION AND MAINTENANCE WORKSHEET")
    c.setFont("Helvetica", 7)
    c.drawCentredString(W / 2, y - 24, "For use of this form, see DA PAM 750-8; the proponent agency is DCS, G-4.")

    # outer border
    form_top = y - 10
    ROWS_H = 12 * inch      # approximate; we'll cap to page
    _box(c, M, M, FW, form_top - M)

    # ── Row 1: Org | Nomenclature ──────────────────────────────────────────────
    r1y = form_top - 30
    mid = M + FW * 0.45
    # org box
    c.rect(M, r1y, FW * 0.45, 30)
    _label(c, M + 2, r1y + 20, "1. ORGANIZATION", size=6, bold=False)
    _value(c, M + 4, r1y + 8, org, size=9)
    # nom box
    c.rect(mid, r1y, FW * 0.55, 30)
    _label(c, mid + 2, r1y + 20, "2. NOMENCLATURE AND MODEL", size=6)
    _value(c, mid + 4, r1y + 8, nom, size=9)

    # ── Row 2: Serial | Miles | Hours | Rounds | Hot Starts | Date | Type ────
    r2y = r1y - 22
    segs = [
        ("3. REGISTRATION/SERIAL/NSN", serial, 0.30),
        ("4a. MILES", "", 0.07),
        ("b. HOURS",  "", 0.07),
        ("c. ROUNDS FIRED", "", 0.09),
        ("d. HOT STARTS",   "", 0.09),
        ("5. DATE", date, 0.18),
        ("6. TYPE INSPECTION", insp_type, 0.20),
    ]
    cx = M
    for label, val, frac in segs:
        bw = FW * frac
        c.rect(cx, r2y, bw, 22)
        _label(c, cx + 2, r2y + 14, label, size=5.5)
        _value(c, cx + 2, r2y + 4, val, size=8)
        cx += bw

    # ── Row 3: TM Reference ────────────────────────────────────────────────────
    r3y = r2y - 20
    c.rect(M, r3y, FW, 20)
    _label(c, M + 2, r3y + 12, "7.    APPLICABLE REFERENCE", size=6)
    # sub-boxes
    tm_cols = [("TM NUMBER", tm_num, 0.25), ("TM DATE", tm_date, 0.25),
               ("TM NUMBER", "", 0.25), ("TM DATE", 0.25, 0.25)]
    tx = M
    for lbl, val, frac in [
        ("TM NUMBER", tm_num, 0.25), ("TM DATE", tm_date, 0.25),
        ("TM NUMBER", "",     0.25), ("TM DATE", "",      0.25)
    ]:
        bw = FW * frac
        c.line(tx, r3y, tx, r3y + 20)
        _label(c, tx + 2, r3y + 11, lbl, size=5.5)
        _value(c, tx + 4, r3y + 3, val, size=8)
        tx += bw

    # ── Legend block ──────────────────────────────────────────────────────────
    r4y = r3y - 50
    c.rect(M, r4y, FW, 50)
    lx, rx = M + 2, M + FW * 0.5 + 2
    legend = [
        (lx, "COLUMN a – Enter TM item number."),
        (lx, "COLUMN b – Enter the applicable condition status symbol."),
        (lx, "COLUMN c – Enter deficiencies and shortcomings."),
    ]
    rlegend = [
        (rx, "COLUMN d – Show corrective action for deficiency or shortcoming listed in Column c."),
        (rx, "COLUMN e – Individual ascertaining completed corrective action initial in this column."),
    ]
    c.setFont("Helvetica", 6.5)
    for i, (x, t) in enumerate(legend):
        c.drawString(x, r4y + 40 - i * 10, t)
    for i, (x, t) in enumerate(rlegend):
        _wrap(c, x, r4y + 40 - i * 14, t, 6.5, FW * 0.46)
    c.line(M + FW * 0.5, r4y, M + FW * 0.5, r4y + 50)

    # ── Status symbols block ──────────────────────────────────────────────────
    r5y = r4y - 64
    c.rect(M, r5y, FW, 64)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W / 2, r5y + 55, "STATUS SYMBOLS")
    c.line(M + FW * 0.5, r5y, M + FW * 0.5, r5y + 64)
    sym_left = [
        '"X" – Indicates a deficiency in the equipment that places it in an inoperable status.',
        'CIRCLED "X" – Indicates a deficiency, however, the equipment may be operated under specific',
        '  limitations as directed by higher authority or as prescribed locally, until corrective',
        '  action can be accomplished.',
        'HORIZONTAL DASH "(-)" – Indicates that a required inspection, component replacement,',
        '  maintenance operation check, or test flight is due but has not been accomplished,',
        '  or an overdue MWO has not been accomplished.',
    ]
    sym_right = [
        'DIAGONAL "(/)" – Indicates a material defect other than a deficiency which must be',
        '  corrected to increase efficiency or to make the item completely serviceable.',
        'LAST NAME INITIAL IN BLACK, BLUE-BLACK INK, OR PENCIL – Indicates that a completely',
        '  satisfactory condition exists.',
        'FOR AIRCRAFT – Status symbols will be recorded in red.',
    ]
    c.setFont("Helvetica", 5.8)
    for i, t in enumerate(sym_left):
        c.drawString(M + 3, r5y + 48 - i * 8, t)
    for i, t in enumerate(sym_right):
        c.drawString(M + FW * 0.5 + 3, r5y + 48 - i * 8, t)

    # ── Certification banner ──────────────────────────────────────────────────
    r6y = r5y - 18
    c.rect(M, r6y, FW, 18)
    c.setFont("Helvetica-BoldOblique", 7)
    c.drawCentredString(W / 2, r6y + 10,
        "ALL INSPECTIONS AND EQUIPMENT CONDITIONS RECORDED ON THIS FORM HAVE BEEN DETERMINED")
    c.drawCentredString(W / 2, r6y + 3,
        "IN ACCORDANCE WITH DIAGNOSTIC PROCEDURES AND STANDARDS IN THE TM CITED HEREON.")

    # ── Signature row ─────────────────────────────────────────────────────────
    r7y = r6y - 36
    sig_segs = [
        ("8a. SIGNATURE (Person(s) performing inspection)", inspector, 0.45),
        ("8b. TIME", insp_time, 0.10),
        ("9a. SIGNATURE    (Maintenance Supervisor)", supervisor, 0.35),
        ("9b. TIME", sup_time, 0.10),
    ]
    # manhours in last box
    sx = M
    for label, val, frac in sig_segs:
        bw = FW * frac
        c.rect(sx, r7y, bw, 36)
        _label(c, sx + 2, r7y + 27, label, size=5.5)
        _value(c, sx + 4, r7y + 12, val, size=9)
        sx += bw
    # manhours box
    mhx = sx
    mhw = FW - (sx - M)
    c.rect(M + FW * 0.99, r7y, FW * 0.01 + 1, 36)   # stub — already included above
    # redraw last segment with manhours label
    c.rect(M, r7y, FW, 36)  # outer border only (inner already drawn)
    # manhours label over last column area (rightmost ~8%)
    mh_x = M + FW * 0.90
    mh_w = FW * 0.10
    c.rect(mh_x, r7y, mh_w, 36)
    _label(c, mh_x + 2, r7y + 27, "10. MANHOURS\nREQUIRED", size=5.5)
    _value(c, mh_x + 4, r7y + 12, manhours, size=9)

    # ── Column headers for data table ─────────────────────────────────────────
    r8y = r7y - 18
    col_w = [FW * f for f in [0.06, 0.07, 0.37, 0.40, 0.10]]
    col_labels = ["TM\nITEM\nNO.\na", "STATUS\n\n\nb",
                  "DEFICIENCIES AND SHORTCOMINGS\n\n\nc",
                  "CORRECTIVE ACTION\n\n\nd",
                  "INITIAL\nWHEN\nCORRECTED\ne"]
    hx = M
    for i, (lbl, cw) in enumerate(zip(col_labels, col_w)):
        c.rect(hx, r8y, cw, 18)
        c.setFont("Helvetica-Bold", 5.5)
        lines = lbl.split("\n")
        for j, ln in enumerate(lines[:3]):
            c.drawCentredString(hx + cw / 2, r8y + 13 - j * 5, ln)
        hx += cw

    # ── Data rows ─────────────────────────────────────────────────────────────
    ROW_H = 18
    data_y = r8y - ROW_H
    # how many rows fit on this page
    min_y = M + 40   # leave room for footer
    rows_available = max(1, int((r8y - min_y) / ROW_H))

    for row_idx in range(rows_available):
        ry = r8y - (row_idx + 1) * ROW_H
        if ry < min_y:
            break
        hx = M
        for cw in col_w:
            c.rect(hx, ry, cw, ROW_H)
            hx += cw

        if row_idx < len(items):
            it = items[row_idx]
            hx = M
            c.setFont("Helvetica", 7)
            c.drawCentredString(hx + col_w[0]/2, ry + 5, str(it.get("item_no", "")))
            hx += col_w[0]
            c.drawCentredString(hx + col_w[1]/2, ry + 5, str(it.get("status", "/")))
            hx += col_w[1]
            _wrap(c, hx + 2, ry + 11, it.get("deficiency", ""), 7, col_w[2] - 4, 9)
            hx += col_w[2]
            _wrap(c, hx + 2, ry + 11, it.get("corrective_action", ""), 7, col_w[3] - 4, 9)
            hx += col_w[3]
            c.drawCentredString(hx + col_w[4]/2, ry + 5, str(it.get("initial", "")))

    # ── Certified inspector footer ─────────────────────────────────────────────
    cert_y = min_y
    c.rect(M, cert_y, FW, 36)
    # dashed line for signature
    c.setDash(3, 2)
    sig_x = M + FW * 0.25
    c.line(sig_x, cert_y + 24, M + FW * 0.85, cert_y + 24)
    c.line(sig_x, cert_y + 8,  M + FW * 0.85, cert_y + 8)
    c.setDash()
    c.setFont("Helvetica", 6.5)
    c.drawString(M + 4, cert_y + 26, "Certified Inspectors Signature / Date =====>")
    c.drawString(M + 4, cert_y + 10, "Print Certified Inspectors Name =====>")
    if inspector:
        c.setFont("Helvetica", 8)
        c.drawString(sig_x + 4, cert_y + 26, inspector)
        c.drawString(sig_x + 4, cert_y + 10, inspector)
        if date:
            c.drawString(M + FW * 0.70, cert_y + 26, date)

    # ── Footer ────────────────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 7)
    c.drawString(M, cert_y - 12, "DA FORM 2404, FEB 2011")
    c.setFont("Helvetica", 7)
    c.drawCentredString(W / 2, cert_y - 12, "PREVIOUS EDITIONS ARE OBSOLETE.")
    c.drawRightString(W - M, cert_y - 12, "APD LC v1.00ES")

    c.showPage()
