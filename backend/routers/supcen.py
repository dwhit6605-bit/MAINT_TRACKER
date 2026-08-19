"""CoMSupCen catalog — read-only reference for the 2,499-item supply catalog.

Ported from the standalone COMSUPCEN prototype. That build ships everything as
one 13.9 MB JS file because it must run from file://; here the rows live in
SQLite and the thumbnails are static files, so search happens server-side and
the browser only fetches the images it actually shows.

Search follows the prototype's rule: terms are ANDed, and when that yields
nothing the narrowest term is dropped and the response says which, so a query
like "nitrile glove medium" still returns the gloves rather than nothing.
"""
import json
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_tech

router = APIRouter(prefix="/api/supcen", tags=["supcen"])

PAGE_SIZE = 60


def _stem(t: str) -> str:
    """Trim a plural 's' so an item named "Gloves" finds a catalogue "GLOVE".

    Only from words of 5+ letters: LIKE %GLOVE% still matches GLOVES, but the
    reverse does not, and searching "gloves nitrile" for zero hits made the
    fallback throw away "nitrile" and return butyl gloves. Short words are left
    alone so GAS does not become GA.
    """
    if len(t) >= 5 and t.endswith("IES"):
        return t[:-3] + "Y"          # BATTERIES -> BATTERY, the catalogue's spelling
    if len(t) >= 5 and t.endswith("S") and not t.endswith("SS"):
        return t[:-1]                # GLOVES -> GLOVE
    return t


def _tokens(q: str):
    return [_stem(t) for t in (q or "").upper().replace(",", " ").split() if t]


def _row(r):
    d = dict(r)
    d["cats"] = json.loads(d.get("cats") or "[]")
    d.pop("search_blob", None)
    return d


async def _count_for(db, terms, where_extra, params_extra):
    sql = "SELECT COUNT(*) FROM supcen_catalog WHERE 1=1" + where_extra
    params = list(params_extra)
    for t in terms:
        sql += " AND search_blob LIKE ?"
        params.append(f"%{t}%")
    async with db.execute(sql, params) as cur:
        return (await cur.fetchone())[0]


@router.get("/catalog")
async def search_catalog(
    q: str = "",
    end_item: str = "",
    classification: str = "",
    org: str = "",
    page: int = 1,
    db=Depends(get_db),
):
    where, params = "", []
    if end_item:
        where += " AND end_item = ?"; params.append(end_item)
    if classification:
        where += " AND classification = ?"; params.append(classification)
    if org:
        where += " AND orgs LIKE ?"; params.append(f"%{org}%")

    terms = _tokens(q)
    dropped = []
    if terms:
        # A term the catalog does not use should not zero out the query. Drop the
        # one whose removal leaves the SMALLEST non-empty result — that keeps the
        # discriminating words ("nitrile", "glove") and sheds the one the catalog
        # spells differently ("medium", where the item is named M/8). Dropping by
        # global rarity gets this backwards: "nitrile" is rarer than "medium".
        while len(terms) > 1 and await _count_for(db, terms, where, params) == 0:
            best = None
            for t in terms:
                trial = [x for x in terms if x != t]
                n = await _count_for(db, trial, where, params)
                if n > 0 and (best is None or n < best[0]):
                    best = (n, t, trial)
            if not best:
                break
            dropped.append(best[1])
            terms = best[2]
        # Single term still matching nothing: report it rather than silently
        if len(terms) == 1 and await _count_for(db, terms, where, params) == 0:
            dropped.append(terms[0])
            terms = []

    sql = "SELECT * FROM supcen_catalog WHERE 1=1" + where
    sp = list(params)
    for t in terms:
        sql += " AND search_blob LIKE ?"
        sp.append(f"%{t}%")

    total = await _count_for(db, terms, where, params)
    page = max(1, page)
    sql += " ORDER BY nom LIMIT ? OFFSET ?"
    sp += [PAGE_SIZE, (page - 1) * PAGE_SIZE]
    async with db.execute(sql, sp) as cur:
        items = [_row(r) for r in await cur.fetchall()]

    return {
        "items": items, "total": total, "page": page,
        "pages": max(1, -(-total // PAGE_SIZE)),
        "dropped_terms": dropped,
    }


@router.get("/facets")
async def facets(db=Depends(get_db)):
    """End items with a count, empties omitted — the prototype hides the 49 that
    would otherwise be dead entries in the picker."""
    async with db.execute("""
        SELECT end_item, COUNT(*) n FROM supcen_catalog
        WHERE end_item <> '' GROUP BY end_item ORDER BY n DESC, end_item
    """) as cur:
        end_items = [dict(r) for r in await cur.fetchall()]
    async with db.execute("""
        SELECT classification, COUNT(*) n FROM supcen_catalog
        WHERE classification <> '' GROUP BY classification ORDER BY n DESC
    """) as cur:
        classes = [dict(r) for r in await cur.fetchall()]
    async with db.execute("SELECT COUNT(*) FROM supcen_catalog") as cur:
        total = (await cur.fetchone())[0]
    return {"end_items": end_items, "classifications": classes, "total": total}


@router.get("/item/{item_id}")
async def get_item(item_id: int, db=Depends(get_db)):
    async with db.execute("SELECT * FROM supcen_catalog WHERE id=?", (item_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return Response(status_code=404)
    return _row(row)


# ── Matching inventory items to catalogue entries ─────────────────────────────
#
# Scoring is IDF-weighted recall (how much of the item name the candidate
# explains) with a precision penalty (how much of the candidate the item name
# does not). Counting matched tokens alone ranks a $370 "RAPID BATTERY CHARGER"
# top for "Batteries - 9v"; the penalty pushes plain 9V cells above it.
#
# It is a ranker, not a decider. "Batteries - AA" still scores 0.845 on a battery
# ADAPTER, so nothing is ever linked without someone confirming it.

import math
import re

_STOP = {"THE", "FOR", "AND", "WITH", "OF", "IN", "PER"}
_INDEX = None


def _tok(s):
    return [_stem(w) for w in re.split(r"[^A-Z0-9]+", str(s or "").upper())
            if len(w) > 1 and w not in _STOP]


async def _index(db):
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    async with db.execute(
        "SELECT id, nom, nsn, mcn, unit_price, unit_issue, image, shelf_life FROM supcen_catalog"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    df = {}
    for r in rows:
        r["_t"] = _tok(r["nom"])
        for w in set(r["_t"]):
            df[w] = df.get(w, 0) + 1
    n = len(rows) or 1
    idf = {w: math.log(n / (c + 1)) + 1 for w, c in df.items()}
    default_idf = math.log(n) + 1
    _INDEX = (rows, idf, default_idf)
    return _INDEX


def _score(q, cand, idf, d):
    qs, cs = set(q), set(cand)
    if not qs:
        return 0.0
    q_tot = sum(idf.get(w, d) for w in qs)
    hit = sum(idf.get(w, d) for w in qs if w in cs)
    c_tot = sum(idf.get(w, d) for w in cs) or 1.0
    extra = sum(idf.get(w, d) for w in cs if w not in qs)
    return (hit / q_tot) * 0.75 + (1 - extra / c_tot) * 0.25


async def _suggest(db, name, limit=5):
    rows, idf, d = await _index(db)
    q = _tok(name)
    if not q:
        return []
    scored = sorted(
        ((_score(q, r["_t"], idf, d), r) for r in rows), key=lambda x: -x[0]
    )[:limit]
    return [{k: v for k, v in r.items() if k != "_t"} | {"score": round(s, 3)}
            for s, r in scored if s > 0]


@router.get("/suggest")
async def suggest(name: str, limit: int = Query(5, ge=1, le=20), db=Depends(get_db)):
    return {"name": name, "candidates": await _suggest(db, name, limit)}


@router.get("/review-queue")
async def review_queue(limit: int = Query(25, ge=1, le=100), offset: int = 0,
                       db=Depends(get_db)):
    """Unlinked inventory items with their top candidates, best guesses first."""
    async with db.execute("""
        SELECT id, name, part_number, quantity, unit FROM inventory_items
        WHERE catalog_id IS NULL AND COALESCE(catalog_none,0) = 0
        ORDER BY name
    """) as cur:
        pending = [dict(r) for r in await cur.fetchall()]

    async with db.execute("""
        SELECT COUNT(*) FROM inventory_items WHERE catalog_id IS NOT NULL
    """) as cur:
        linked = (await cur.fetchone())[0]
    async with db.execute("""
        SELECT COUNT(*) FROM inventory_items WHERE COALESCE(catalog_none,0) = 1
    """) as cur:
        marked_none = (await cur.fetchone())[0]

    out = []
    for it in pending[offset:offset + limit]:
        cands = await _suggest(db, it["name"], 5)
        out.append({**it, "candidates": cands,
                    "top_score": cands[0]["score"] if cands else 0})
    out.sort(key=lambda x: -x["top_score"])
    return {"items": out, "pending": len(pending), "linked": linked,
            "marked_none": marked_none, "offset": offset}


class LinkBody(BaseModel):
    catalog_id: Optional[int] = None
    not_in_catalog: bool = False


@router.post("/link/{item_id}")
async def link_item(item_id: int, data: LinkBody, request: Request, db=Depends(get_db)):
    """Attach a catalogue entry, or record that there isn't one.

    The link is the source of truth; part_number and unit_cost are copied so the
    modules already reading those columns keep working unchanged.
    """
    require_tech(request)
    async with db.execute("SELECT id FROM inventory_items WHERE id=?", (item_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Item not found")

    if data.not_in_catalog:
        await db.execute(
            "UPDATE inventory_items SET catalog_id=NULL, catalog_none=1,"
            " updated_at=datetime('now') WHERE id=?", (item_id,))
        await db.commit()
        return {"ok": True, "state": "not_in_catalog"}

    if data.catalog_id is None:
        raise HTTPException(400, "Provide catalog_id or not_in_catalog")
    async with db.execute(
        "SELECT nsn, mcn, unit_price FROM supcen_catalog WHERE id=?", (data.catalog_id,)
    ) as cur:
        c = await cur.fetchone()
    if not c:
        raise HTTPException(404, "Catalogue item not found")

    part = c["nsn"] or c["mcn"] or None
    await db.execute("""
        UPDATE inventory_items
        SET catalog_id=?, catalog_none=0,
            part_number=COALESCE(NULLIF(part_number,''), ?),
            unit_cost=COALESCE(unit_cost, ?),
            updated_at=datetime('now')
        WHERE id=?
    """, (data.catalog_id, part, c["unit_price"], item_id))
    await db.commit()
    return {"ok": True, "state": "linked", "part_number": part,
            "unit_cost": c["unit_price"]}


@router.delete("/link/{item_id}")
async def unlink_item(item_id: int, request: Request, db=Depends(get_db)):
    """Clear the link and the not-in-catalogue flag. Copied part number and cost
    are left alone — they may have been entered by hand."""
    require_tech(request)
    await db.execute(
        "UPDATE inventory_items SET catalog_id=NULL, catalog_none=0,"
        " updated_at=datetime('now') WHERE id=?", (item_id,))
    await db.commit()
    return {"ok": True}


@router.post("/resync/{item_id}")
async def resync_item(item_id: int, request: Request, db=Depends(get_db)):
    """Overwrite part number and cost from the linked catalogue entry."""
    require_tech(request)
    async with db.execute("""
        SELECT c.nsn, c.mcn, c.unit_price FROM inventory_items i
        JOIN supcen_catalog c ON c.id = i.catalog_id WHERE i.id=?
    """, (item_id,)) as cur:
        c = await cur.fetchone()
    if not c:
        raise HTTPException(404, "Item is not linked to a catalogue entry")
    part = c["nsn"] or c["mcn"] or None
    await db.execute(
        "UPDATE inventory_items SET part_number=?, unit_cost=?,"
        " updated_at=datetime('now') WHERE id=?", (part, c["unit_price"], item_id))
    await db.commit()
    return {"ok": True, "part_number": part, "unit_cost": c["unit_price"]}


@router.post("/auto-link-codes")
async def auto_link_codes(request: Request, db=Depends(get_db)):
    """The one safe automatic case: an item whose part number is already an exact
    NSN/MCN in the catalogue. Never guesses on names."""
    require_tech(request)
    async with db.execute("""
        SELECT i.id, i.part_number, c.id AS cid
        FROM inventory_items i
        JOIN supcen_catalog c
          ON (c.nsn <> '' AND c.nsn = i.part_number)
          OR (c.mcn <> '' AND c.mcn = i.part_number)
        WHERE i.catalog_id IS NULL AND COALESCE(i.part_number,'') <> ''
    """) as cur:
        hits = [dict(r) for r in await cur.fetchall()]
    for h in hits:
        await db.execute(
            "UPDATE inventory_items SET catalog_id=?, catalog_none=0 WHERE id=?",
            (h["cid"], h["id"]))
    await db.commit()
    return {"linked": len(hits)}
