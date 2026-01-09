#!/usr/bin/env python3
"""
visitzuidlimburg.nl sitemap scraper

Doel:
- Lees ALLE records uit https://www.visitzuidlimburg.nl/sitemap.xml (± 3600/3700)
- Schrijf alles naar CSV (url, lastmod, type, naam_afgeleid)
- Optioneel: probeer per pagina een "Direct boeken / Boek nu / Naar de website" (externe) link te vinden
  - eerst uit gewone HTML (requests)
  - daarna (optioneel) via JS-render (Playwright) als de link dynamisch geladen wordt

Gebruik (Codespaces):
  python -m pip install -r requirements.txt
  # als je --js gebruikt:
  python -m playwright install chromium

Run:
  # alleen sitemap -> csv (snel)
  python main.py --out sitemap_all.csv

  # sitemap + outbound links uit HTML (detailpagina's)
  python main.py --enrich --out sitemap_all_enriched.csv

  # sitemap + outbound links + JS fallback (detailpagina's)
  python main.py --enrich --js --out sitemap_all_enriched_js.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


SITEMAP_URL = "https://www.visitzuidlimburg.nl/sitemap.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VisitZuidLimburgScraper/4.0; +https://github.com/)",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

# Teksten die vaak bij "boek"-knoppen horen
KEYWORDS = [
    "direct boeken",
    "boek nu",
    "boeken",
    "reserveren",
    "naar de website",
    "website",
    "booking",
    "reserveer",
    "book",
]

# Data-attributen waar sites vaak een externe URL in verstoppen
DATA_ATTRS = [
    "data-href",
    "data-url",
    "data-link",
    "data-booking",
    "data-booking-url",
    "data-external-url",
    "data-cta-url",
    "data-target-url",
    "data-redirect",
    "data-redirect-url",
]


@dataclass
class Row:
    url: str
    lastmod: str
    type: str
    naam_afgeleid: str
    outbound_label: str
    outbound_url: str
    outbound_source: str  # none | html | rendered | error


def fetch_text(session: requests.Session, url: str, timeout: int) -> str:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_sitemap(xml_text: str) -> list[tuple[str, str]]:
    """
    Leest <loc> en <lastmod> uit sitemap.xml.
    Werkt ook als er een namespace gebruikt wordt.
    """
    root = ET.fromstring(xml_text)
    # namespace
    ns = ""
    m = re.match(r"\{(.*)\}", root.tag)
    if m:
        ns = m.group(1)

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}" if ns else tag

    out: list[tuple[str, str]] = []
    for url_el in root.findall(q("url")):
        loc_el = url_el.find(q("loc"))
        lm_el = url_el.find(q("lastmod"))
        loc = (loc_el.text or "").strip() if loc_el is not None else ""
        lastmod = (lm_el.text or "").strip() if lm_el is not None else ""
        if loc:
            out.append((loc, lastmod))
    return out


def classify(url: str) -> str:
    # grof type op basis van pad (u kunt dit uitbreiden)
    if "/overnachten/hotels/detail/" in url:
        return "hotel-detail"
    if "/overnachten/bed-breakfasts/detail/" in url:
        return "bb-detail"
    if "/overnachten/" in url and "/detail/" in url:
        return "overnachten-detail"
    if "/overnachten/" in url:
        return "overnachten"
    if "/eten-drinken/" in url and "/detail/" in url:
        return "eten-drinken-detail"
    if "/eten-drinken/" in url:
        return "eten-drinken"
    if "/doen/" in url and "/detail/" in url:
        return "doen-detail"
    if "/doen/" in url:
        return "doen"
    if "/zien-beleven/" in url and "/detail/" in url:
        return "zien-beleven-detail"
    if "/zien-beleven/" in url:
        return "zien-beleven"
    if "/govisit/" in url:
        return "govisit"
    return "overig"


def name_from_url(url: str) -> str:
    parts = [p for p in url.split("/") if p]
    if not parts:
        return ""
    last = parts[-1]
    # vaak .../<slug>/<id>/
    if last.isdigit() and len(parts) >= 2:
        slug = parts[-2]
    else:
        slug = last
    words = slug.replace("-", " ").strip().split()
    out = []
    for w in words:
        lw = w.lower()
        if lw in {"bb", "b&b", "benb"}:
            out.append("B&B")
        else:
            out.append(w.capitalize())
    return " ".join(out)


def is_external(candidate: str) -> bool:
    try:
        host = urlparse(candidate).netloc.lower()
        return bool(host) and "visitzuidlimburg.nl" not in host
    except Exception:
        return False


def find_outbound_in_soup(soup: BeautifulSoup, base_url: str) -> tuple[str, str]:
    """
    Probeert de 'echte' externe link te vinden (Direct boeken / website).
    """
    # 1) Normale anchors met relevante tekst
    for a in soup.find_all("a", href=True):
        txt = (a.get_text(" ", strip=True) or "").strip()
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        if txt and any(k in txt.lower() for k in KEYWORDS) and is_external(abs_url):
            return (txt, abs_url)

    # 2) data-attributes op knoppen/divs/a
    for tag in soup.find_all(True):
        txt = (tag.get_text(" ", strip=True) or "").strip()
        if txt and not any(k in txt.lower() for k in KEYWORDS):
            continue
        for attr in DATA_ATTRS:
            v = tag.get(attr)
            if v:
                abs_url = urljoin(base_url, str(v).strip())
                if is_external(abs_url):
                    return (txt or attr, abs_url)

    # 3) onclick="window.open('https://...')" of location.href='...'
    for tag in soup.find_all(True):
        onclick = (tag.get("onclick") or "").strip()
        if not onclick:
            continue
        if any(k in onclick.lower() for k in KEYWORDS) or any(k in (tag.get_text(" ", strip=True) or "").lower() for k in KEYWORDS):
            m = re.search(r"(https?://[^\s'\"\\)]+)", onclick)
            if m and is_external(m.group(1)):
                label = (tag.get_text(" ", strip=True) or "onclick").strip()
                return (label, m.group(1))

    # 4) script fallback: zoek eerste externe URL in scripts (soms zit boekinglink in JSON)
    for script in soup.find_all("script"):
        s = (script.get_text(" ", strip=True) or "")
        m = re.search(r"https?://(?!www\.visitzuidlimburg\.nl)[^\s\"']+", s)
        if m and is_external(m.group(0)):
            return ("script-url", m.group(0))

    return ("", "")


def extract_outbound_from_html(html: str, base_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    return find_outbound_in_soup(soup, base_url)


def fetch_rendered_html(url: str, timeout_ms: int) -> str:
    """
    Laadt pagina met JS (Playwright) en retourneert gerenderde HTML.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError("Playwright niet geïnstalleerd. Run: python -m pip install playwright") from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # (optioneel) consent knop proberen weg te klikken
        for txt in ["Akkoord", "Accepteer", "Accept", "Alles accepteren"]:
            try:
                btn = page.get_by_role("button", name=txt)
                if btn.count() > 0:
                    btn.first.click(timeout=1500)
                    break
            except Exception:
                pass

        page.wait_for_timeout(800)
        html = page.content()
        browser.close()
        return html


def should_enrich(url: str, enrich_all: bool) -> bool:
    """
    U wilt ALLE sitemap records in CSV.
    'Enrich' (outbound zoeken) is alleen zinvol op detail/overnachten/eten-drinken e.d.
    Met --enrich-all probeert hij het op alles (veel zwaarder).
    """
    if enrich_all:
        return True
    # standaard: alleen waar kans reëel is op outbound
    return ("/detail/" in url) or ("/overnachten/" in url)


def should_js(url: str, js_all: bool) -> bool:
    """
    JS-render is zwaar. Standaard alleen detailpagina's.
    """
    if js_all:
        return True
    return "/detail/" in url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sitemap_all_enriched_js.csv")
    ap.add_argument("--enrich", action="store_true", help="probeer outbound link te vinden")
    ap.add_argument("--js", action="store_true", help="als outbound niet in HTML zit: probeer JS-render (Playwright)")
    ap.add_argument("--limit", type=int, default=0, help="0 = alles; anders max aantal records")
    ap.add_argument("--delay", type=float, default=0.4, help="pauze tussen requests (sec)")
    ap.add_argument("--timeout", type=int, default=30, help="requests timeout (sec)")
    ap.add_argument("--render-timeout", type=int, default=60000, help="Playwright goto timeout (ms)")
    ap.add_argument("--enrich-all", action="store_true", help="probeer outbound op alle pagina's (zwaar)")
    ap.add_argument("--js-all", action="store_true", help="probeer JS-render op alle pagina's (zeer zwaar)")
    args = ap.parse_args()

    session = requests.Session()

    print("Sitemap ophalen…")
    xml = fetch_text(session, SITEMAP_URL, timeout=args.timeout)
    entries = parse_sitemap(xml)

    if args.limit and args.limit > 0:
        entries = entries[: args.limit]

    total = len(entries)
    print(f"Aantal sitemap records: {total}")

    rows: list[Row] = []

    for i, (url, lastmod) in enumerate(entries, start=1):
        rtype = classify(url)
        naam = name_from_url(url)

        outbound_label = ""
        outbound_url = ""
        outbound_source = "none"

        if args.enrich and should_enrich(url, args.enrich_all):
            try:
                html = fetch_text(session, url, timeout=args.timeout)
                outbound_label, outbound_url = extract_outbound_from_html(html, url)
                outbound_source = "html" if outbound_url else "none"

                if args.js and (not outbound_url) and should_js(url, args.js_all):
                    rendered = fetch_rendered_html(url, timeout_ms=args.render_timeout)
                    outbound_label, outbound_url = extract_outbound_from_html(rendered, url)
                    outbound_source = "rendered" if outbound_url else outbound_source

            except Exception as e:
                outbound_label = "ERROR"
                outbound_url = f"{type(e).__name__}: {e}"
                outbound_source = "error"

            time.sleep(max(0.0, args.delay))

        if i % 100 == 0 or i == total:
            print(f"… verwerkt: {i}/{total}")

        rows.append(
            Row(
                url=url,
                lastmod=lastmod,
                type=rtype,
                naam_afgeleid=naam,
                outbound_label=outbound_label,
                outbound_url=outbound_url,
                outbound_source=outbound_source,
            )
        )

    print(f"CSV schrijven: {args.out}")
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "url",
                "lastmod",
                "type",
                "naam_afgeleid",
                "outbound_label",
                "outbound_url",
                "outbound_source",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.url,
                    r.lastmod,
                    r.type,
                    r.naam_afgeleid,
                    r.outbound_label,
                    r.outbound_url,
                    r.outbound_source,
                ]
            )

    print("Klaar.")


if __name__ == "__main__":
    main()
