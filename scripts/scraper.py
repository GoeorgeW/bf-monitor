"""
scraper.py - Extracción ligera de precios.

Orden de prioridad:
  1. JSON-LD (application/ld+json): Schema.org Product -> offers -> price / availability
  2. Metaetiquetas OpenGraph / product:price:amount / itemprop=price
  3. Fallback: selectores HTML conocidos + regex de euros (€)
Además detecta banners de cupones/promos en el texto visible.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Iterator, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

# Proxy opcional para webs que bloquean IPs de datacenter (p.ej. http://user:pass@host:port)
SCRAPER_PROXY = os.getenv("SCRAPER_PROXY") or None


class ScrapeError(Exception):
    pass


@dataclass
class ScrapeResult:
    title: Optional[str] = None
    image: Optional[str] = None
    price: Optional[float] = None
    currency: str = "EUR"
    in_stock: Optional[bool] = None
    coupon: Optional[str] = None
    source: Optional[str] = None          # 'ld+json' | 'meta' | 'fallback'
    size_matched: Optional[bool] = None   # True/False si se pidió talla; None si no verificable
    error: Optional[str] = None


# --------------------------------------------------------------------------
# Seguridad (anti-SSRF) y descarga
# --------------------------------------------------------------------------
async def assert_public_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ScrapeError("URL no válida (usa http/https).")
    if os.getenv("ALLOW_PRIVATE") == "1":  # solo para tests locales
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, p.hostname, None)
    except socket.gaierror:
        raise ScrapeError("No se pudo resolver el dominio.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ScrapeError("URL no permitida (red privada).")


def _headers(ua: str) -> dict:
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "no-cache",
    }


async def fetch_html(url: str) -> tuple[str, str]:
    """Descarga el HTML probando UA de escritorio y de móvil. Devuelve (html, url_final)."""
    last_err = "No se pudo descargar la página."
    for ua in (UA_DESKTOP, UA_MOBILE):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=False,
                proxy=SCRAPER_PROXY,
            ) as client:
                current = url
                for _ in range(6):  # redirecciones manuales (revisando cada salto)
                    await assert_public_url(current)
                    r = await client.get(current, headers=_headers(ua))
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    break
                else:
                    last_err = "Demasiadas redirecciones."
                    continue
                if r.status_code == 200 and len(r.text) > 60:
                    return r.text, str(r.url)
                last_err = f"La tienda respondió HTTP {r.status_code} (posible bloqueo anti-bot)."
        except ScrapeError:
            raise
        except httpx.HTTPError as e:
            last_err = f"Error de red: {type(e).__name__}"
    raise ScrapeError(last_err)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def parse_price(v: Any) -> Optional[float]:
    """Convierte '1.299,99 €', '129.99', 89, '89,95' ... a float."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = re.sub(r"[^\d.,]", "", str(v))
        if not s or not re.search(r"\d", s):
            return None
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            head, _, tail = s.rpartition(",")
            s = (head.replace(",", "") + "." + tail) if len(tail) in (1, 2) else s.replace(",", "")
        elif "." in s:
            head, _, tail = s.rpartition(".")
            if len(tail) == 3 or s.count(".") > 1:  # 1.299 / 1.299.000 -> miles
                s = s.replace(".", "")
        try:
            f = float(s)
        except ValueError:
            return None
    return round(f, 2) if 0 < f < 100000 else None


def _availability(v: Any) -> Optional[bool]:
    if not v:
        return None
    s = str(v).lower()
    if any(k in s for k in ("outofstock", "soldout", "discontinued", "out of stock")):
        return False
    if any(k in s for k in ("instock", "limitedavailability", "onlineonly", "preorder", "in stock")):
        return True
    return None


def _first_str(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, list):
        for x in v:
            r = _first_str(x)
            if r:
                return r
    if isinstance(v, dict):
        return _first_str(v.get("url") or v.get("contentUrl") or v.get("@id"))
    return None


def _walk(node: Any) -> Iterator[dict]:
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)


def _is_product(d: dict) -> bool:
    t = d.get("@type")
    ts = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.endswith("Product") or x == "ProductGroup" for x in ts)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _size_regex(size: str) -> Optional[re.Pattern]:
    tokens = re.findall(r"\d+(?:[.,]\d)?|[A-Za-z]+", size)
    if not tokens:
        return None
    core = tokens[0].replace(",", ".")
    if core.isdigit() or re.match(r"^\d+(\.\d)?$", core):
        return re.compile(r"(?<![\d.,])" + re.escape(core).replace(r"\.", "[.,]") + r"(?![\d])", re.I)
    return re.compile(r"\b" + re.escape(core) + r"\b", re.I)


# --------------------------------------------------------------------------
# 1) JSON-LD
# --------------------------------------------------------------------------
def _iter_jsonld(soup: BeautifulSoup) -> Iterator[Any]:
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            try:
                yield json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue


def _flatten_offers(offers: Any, ctx: str) -> list[dict]:
    """Devuelve [{price, avail, ctx, currency}] desde offers (dict/list/AggregateOffer)."""
    out: list[dict] = []
    if isinstance(offers, list):
        for o in offers:
            out += _flatten_offers(o, ctx)
    elif isinstance(offers, dict):
        octx = " ".join(_norm(offers.get(k)) for k in ("name", "sku", "mpn", "description", "size", "url"))
        octx = f"{ctx} {octx}"
        if offers.get("offers"):
            out += _flatten_offers(offers["offers"], octx)
        price = offers.get("price")
        if price is None:
            ps = offers.get("priceSpecification")
            for spec in ps if isinstance(ps, list) else [ps]:
                if isinstance(spec, dict) and spec.get("price") is not None:
                    price = spec["price"]
                    break
        if price is None:
            price = offers.get("lowPrice")
        p = parse_price(price)
        if p is not None:
            out.append({
                "price": p,
                "avail": _availability(offers.get("availability")),
                "ctx": octx,
                "currency": offers.get("priceCurrency") or "EUR",
            })
    return out


def from_jsonld(soup: BeautifulSoup, size: str) -> Optional[dict]:
    candidates: list[dict] = []
    meta: dict = {}
    for block in _iter_jsonld(soup):
        for d in _walk(block):
            if not _is_product(d):
                continue
            meta.setdefault("title", _first_str(d.get("name")))
            meta.setdefault("image", _first_str(d.get("image")))
            base_ctx = " ".join(_norm(d.get(k)) for k in ("name", "sku", "size"))
            candidates += _flatten_offers(d.get("offers"), base_ctx)
    if not candidates:
        return None

    size_matched: Optional[bool] = None
    chosen = candidates
    rx = _size_regex(size) if size else None
    if rx:
        matched = [c for c in candidates if rx.search(c["ctx"])]
        if matched:
            chosen, size_matched = matched, True
        elif len({(c["price"], c["ctx"]) for c in candidates}) > 1:
            size_matched = False  # hay variantes pero no pudimos identificar la talla

    in_stock_offers = [c for c in chosen if c["avail"] is True]
    pool = in_stock_offers or chosen
    best = min(pool, key=lambda c: c["price"])
    avails = [c["avail"] for c in chosen if c["avail"] is not None]
    avail = True if in_stock_offers else (False if avails and not any(avails) else None)
    return {
        "price": best["price"], "currency": best["currency"], "in_stock": avail,
        "size_matched": size_matched, **meta,
    }


# --------------------------------------------------------------------------
# 2) Meta tags
# --------------------------------------------------------------------------
def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) \
            or soup.find("meta", attrs={"itemprop": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def from_meta(soup: BeautifulSoup) -> Optional[dict]:
    price = parse_price(_meta(soup, "product:price:amount", "og:price:amount", "price", "product:sale_price:amount"))
    if price is None:
        return None
    return {
        "price": price,
        "currency": _meta(soup, "product:price:currency", "og:price:currency", "priceCurrency") or "EUR",
        "in_stock": _availability(_meta(soup, "product:availability", "og:availability", "availability")),
    }


# --------------------------------------------------------------------------
# 3) Fallback (selectores + regex de euros)
# --------------------------------------------------------------------------
EURO_RE = re.compile(
    r"(?:€\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?))|"
    r"(?:(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR\b))"
)
SITE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-offscreen", "#corePrice_feature_div .a-offscreen",
    "span.a-price span.a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice",   # Amazon
    "[data-testid='currentPrice-container']", "[data-test='product-price']",            # Nike
    "[itemprop='price']", "[data-price]", "[class*='current-price']", "[class*='product-price']",
    "[class*='price']",
]
BAD_CLASS = re.compile(r"old|was|strike|before|original|regular|list|unit|shipping|envio|ahorr", re.I)
OOS_WORDS = ("agotado", "sin stock", "sin existencias", "no disponible", "out of stock",
             "sold out", "temporalmente sin")
IN_WORDS = ("añadir al carrito", "añadir a la cesta", "añadir a la bolsa", "add to cart",
            "add to bag", "en stock", "in stock", "comprar ahora")


def _euro_from_text(text: str) -> Optional[float]:
    m = EURO_RE.search(text)
    return parse_price(m.group(1) or m.group(2)) if m else None


def from_fallback(soup: BeautifulSoup) -> Optional[dict]:
    price = None
    for sel in SITE_SELECTORS:
        for el in soup.select(sel)[:15]:
            if el.find_parent(["del", "s", "strike"]):
                continue
            cls = " ".join(el.get("class", [])) + " " + " ".join((el.parent.get("class", []) if el.parent else []))
            if BAD_CLASS.search(cls) and "current" not in cls:
                continue
            raw = el.get("content") or el.get("data-price") or el.get_text(" ", strip=True)
            price = parse_price(raw) if (el.get("content") or el.get("data-price")) else _euro_from_text(raw)
            if price:
                break
        if price:
            break
    if not price:
        body = soup.find("body") or soup
        for t in body.find_all(["script", "style", "noscript", "del", "s", "strike"]):
            t.decompose()
        for t in body.find_all(class_=BAD_CLASS):  # precios tachados / antiguos / envío
            t.decompose()
        price = _euro_from_text(body.get_text(" ", strip=True)[:150000])
    return {"price": price, "currency": "EUR"} if price else None


def detect_stock_text(soup: BeautifulSoup) -> Optional[bool]:
    av = soup.select_one("#availability")  # Amazon
    text = (av.get_text(" ", strip=True) if av else "").lower() or soup.get_text(" ", strip=True)[:80000].lower()
    if any(w in text for w in OOS_WORDS):
        return False
    if any(w in text for w in IN_WORDS):
        return True
    return None


# --------------------------------------------------------------------------
# Cupones / promos
# --------------------------------------------------------------------------
PCT_RE = re.compile(r"(?i)\b(\d{1,2})\s?%\s*(?:de\s*)?(?:dto\.?|descuento|off|extra|menos)")
CODE_RE = re.compile(r"(?i:c[oó]digo|cup[oó]n|promo(?:\s*code)?|code)\s*[:\-]?\s*[\"“'«]?([A-Z0-9]{4,16})\b")


def detect_coupon(soup: BeautifulSoup) -> Optional[str]:
    text = soup.get_text(" ", strip=True)[:200000]
    parts = []
    m = PCT_RE.search(text)
    if m:
        parts.append(f"{m.group(1)}% dto")
    for c in CODE_RE.finditer(text):
        code = c.group(1)
        if re.search(r"[A-Z]", code) and code not in ("HTTP", "HTML"):
            parts.append(f"código {code}")
            break
    return " · ".join(parts)[:80] or None


# --------------------------------------------------------------------------
# Punto de entrada
# --------------------------------------------------------------------------
async def scrape(url: str, size: str = "") -> ScrapeResult:
    html, final_url = await fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    res = ScrapeResult()

    # Título e imagen (OpenGraph primero)
    res.title = _meta(soup, "og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    img = _meta(soup, "og:image", "og:image:url", "twitter:image")
    ld = from_jsonld(soup, size)
    if not img and ld:
        img = ld.get("image")
    res.image = urljoin(final_url, img) if img else None

    data = ld or from_meta(soup)
    res.source = "ld+json" if ld else ("meta" if data else None)
    if not data:
        data = from_fallback(BeautifulSoup(html, "lxml"))  # copia limpia (el fallback modifica el árbol)
        res.source = "fallback" if data else None

    if data:
        res.price = data.get("price")
        res.currency = data.get("currency") or "EUR"
        res.in_stock = data.get("in_stock")
        res.size_matched = data.get("size_matched")
        if not res.title and data.get("title"):
            res.title = data["title"]
    if res.in_stock is None:
        res.in_stock = detect_stock_text(BeautifulSoup(html, "lxml"))
    res.coupon = detect_coupon(BeautifulSoup(html, "lxml"))
    if res.price is None:
        res.error = "No se encontró ningún precio en la página (puede requerir JavaScript o estar bloqueada)."
    return res
