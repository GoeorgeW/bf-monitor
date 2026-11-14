"""
update.py - Lo ejecuta GitHub Actions.
  python scripts/update.py              -> revisa todos los precios
  python scripts/update.py issue        -> procesa la petición (issue) de añadir/editar/borrar
Guarda todo en data/data.json (histórico incluido) y avisa por ntfy.sh.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from scraper import ScrapeError, ScrapeResult, scrape  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "data.json"
MAX_POINTS = 500


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    d = json.loads(DATA.read_text("utf-8")) if DATA.exists() else {}
    d.setdefault("config", {"ntfy_topic": "", "notify_min_drop": True})
    d.setdefault("products", [])
    return d


def save(d: dict) -> None:
    d["updated"] = now()
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")


def eur(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


# ---------------- etiquetas anti-falsas ofertas ----------------
def compute_status(p: dict) -> dict:
    price, target, hist = p.get("price"), p.get("target_price"), p.get("history") or []
    if price is None:
        return {"code": "NODATA", "label": "SIN DATOS", "hint": "Aún no se pudo leer el precio."}
    if p.get("in_stock") is False:
        return {"code": "OOS", "label": "SIN STOCK", "hint": "Agotado en la tienda."}
    prices = [h["p"] for h in hist] or [price]
    prev = prices[:-1]
    if target and price <= target:
        return {"code": "CHOLLO", "label": "CHOLLO REAL", "hint": "Igual o por debajo de tu precio objetivo."}
    if len(prev) >= 3 and price <= min(prev) and price < statistics.mean(prev) * 0.98:
        return {"code": "CHOLLO", "label": "CHOLLO REAL", "hint": "Nuevo mínimo histórico registrado."}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    recent = [h["p"] for h in hist if h["t"] >= cutoff] or prices
    prev_distinct = next((x for x in reversed(prev) if abs(x - price) > 0.005), None)
    if len(prices) >= 2 and (price >= min(recent) * 1.05 or (prev_distinct and price >= prev_distinct * 1.03)):
        return {"code": "INFLADO", "label": "PRECIO INFLADO", "hint": "Ha subido respecto a registros recientes. Espera."}
    if len(prices) >= 2 and price < statistics.mean(prices) * 0.99:
        return {"code": "LEVE", "label": "BAJADA LEVE", "hint": "Por debajo de la media, pero lejos del objetivo."}
    return {"code": "NEUTRAL", "label": "RECOPILANDO" if len(prices) < 3 else "SIN CAMBIOS",
            "hint": "Sin variaciones relevantes todavía."}


# ---------------- ntfy ----------------
async def ntfy(cfg: dict, title: str, msg: str, click: Optional[str] = None, tags=None) -> bool:
    topic = os.getenv("NTFY_TOPIC") or cfg.get("ntfy_topic")
    if not topic:
        return False
    payload = {"topic": topic, "title": title, "message": msg, "priority": 4, "tags": tags or ["moneybag"]}
    if click:
        payload["click"] = click
        payload["actions"] = [{"action": "view", "label": "Ver oferta", "url": click}]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            return (await c.post(os.getenv("NTFY_SERVER", "https://ntfy.sh"), json=payload)).status_code == 200
    except httpx.HTTPError:
        return False


# ---------------- revisión de un producto ----------------
async def check(p: dict, cfg: dict, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            r = await scrape(p["url"], p.get("size") or "")
        except ScrapeError as e:
            r = ScrapeResult(error=str(e))
        except Exception as e:
            r = ScrapeResult(error=f"Error inesperado: {type(e).__name__}")
    ts = now()
    p["last_checked"] = ts
    hist = p.setdefault("history", [])
    if r.price is None:
        p["last_error"] = r.error
        p["status"] = compute_status(p)
        return
    prev_min = min((h["p"] for h in hist), default=None)
    n_prev = len(hist)
    stale = not hist or hist[-1]["t"] < (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    if not hist or abs(hist[-1]["p"] - r.price) > 0.004 or stale:
        hist.append({"t": ts, "p": r.price})
        del hist[:-MAX_POINTS]
    p.update(price=r.price, currency=r.currency, in_stock=r.in_stock, coupon=r.coupon, size_matched=r.size_matched,
             source=r.source, last_error=None, image=r.image or p.get("image"), title=r.title or p.get("title"),
             min_price=min(h["p"] for h in hist), avg_price=round(statistics.mean(h["p"] for h in hist), 2))
    p["status"] = compute_status(p)

    # avisos
    target, la, ok_stock = p.get("target_price"), p.get("last_alert_price"), r.in_stock is not False
    size_txt = f" (talla {p['size']})" if p.get("size") else ""
    extra = (f"\nCupón: {r.coupon}" if r.coupon else "") + ("\n⚠ No pude verificar tu talla." if r.size_matched is False else "")
    if target and r.price <= target and ok_stock:
        if la is None or r.price < la - 0.005:
            if await ntfy(cfg, f"🔥 {p['name']}{size_txt}: {eur(r.price)}",
                          f"Objetivo {eur(target)} alcanzado. Mínimo anterior: {eur(prev_min)}.{extra}", p["url"], ["fire"]):
                p["last_alert_price"] = r.price
        return
    if target and r.price > target:
        p["last_alert_price"] = None
    if cfg.get("notify_min_drop") and ok_stock and prev_min and n_prev >= 3 and r.price < prev_min * 0.99:
        await ntfy(cfg, f"📉 Nuevo mínimo: {p['name']}{size_txt}",
                   f"{eur(r.price)} (antes {eur(prev_min)}). Objetivo: {eur(target)}.{extra}", p["url"],
                   ["chart_with_downwards_trend"])


async def check_all(d: dict, only: Optional[list] = None) -> None:
    sem = asyncio.Semaphore(3)
    items = [p for p in d["products"] if only is None or p["id"] in only]
    await asyncio.gather(*(check(p, d["config"], sem) for p in items))


# ---------------- peticiones desde la app (issues) ----------------
def parse_form(body: str) -> dict:
    """Lee el cuerpo de un issue form: '### Campo\\n\\nvalor'."""
    out = {}
    for m in re.finditer(r"^###\s+(.+?)\s*\n+(.*?)(?=^###\s|\Z)", body or "", re.S | re.M):
        val = m.group(2).strip()
        out[m.group(1).strip().lower()] = "" if val == "_No response_" else val
    return out


def num(v: str) -> Optional[float]:
    try:
        f = float(str(v).replace("€", "").replace(",", ".").strip())
        return f if f > 0 else None
    except ValueError:
        return None


async def handle_issue(d: dict) -> str:
    title = os.getenv("ISSUE_TITLE", "")
    f = parse_form(os.getenv("ISSUE_BODY", ""))
    get = lambda *keys: next((f[k] for k in f for key in keys if k.startswith(key)), "")
    if title.lower().startswith("[añadir]"):
        url = get("url").strip()
        if not re.match(r"^https?://", url):
            return "❌ La URL no es válida (debe empezar por https://)."
        pid = max((p["id"] for p in d["products"]), default=0) + 1
        p = {"id": pid, "url": url, "name": (get("nombre") or "Producto")[:80], "size": get("talla")[:30],
             "target_price": num(get("precio")), "created_at": now(), "history": []}
        d["products"].insert(0, p)
        await check_all(d, [pid])
        return (f"✅ Añadido **{p['name']}**. Precio actual: {eur(p.get('price'))}"
                + (f"\n\n⚠ {p['last_error']}" if p.get("last_error") else "")
                + "\n\nAparecerá en la app en 1-2 minutos.")
    pid = int(num(get("número", "numero", "id")) or 0)
    p = next((x for x in d["products"] if x["id"] == pid), None)
    if not p:
        return f"❌ No existe ningún producto con número {pid}."
    if title.lower().startswith("[borrar]"):
        d["products"].remove(p)
        return f"🗑 Eliminado **{p['name']}**."
    if title.lower().startswith("[editar]"):
        if get("nombre"):
            p["name"] = get("nombre")[:80]
        if get("talla"):
            p["size"] = get("talla")[:30]
        if num(get("precio")):
            p["target_price"] = num(get("precio"))
        p["last_alert_price"] = None
        await check_all(d, [pid])
        return f"✏️ Actualizado **{p['name']}** (objetivo {eur(p.get('target_price'))})."
    return "❓ Petición no reconocida."


async def main() -> None:
    d = load()
    if len(sys.argv) > 1 and sys.argv[1] == "issue":
        msg = await handle_issue(d)
        Path(os.getenv("RUNNER_TEMP", "/tmp"), "reply.md").write_text(msg, "utf-8")
        print(msg)
    else:
        await check_all(d)
        print(f"Revisados {len(d['products'])} productos")
    save(d)


if __name__ == "__main__":
    asyncio.run(main())
