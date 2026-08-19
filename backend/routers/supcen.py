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
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from backend.database import get_db

router = APIRouter(prefix="/api/supcen", tags=["supcen"])

PAGE_SIZE = 60


def _tokens(q: str):
    return [t for t in (q or "").upper().replace(",", " ").split() if t]


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
