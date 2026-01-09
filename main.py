import re
import csv
import time
import argparse
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.visitzuidlimburg.nl/sitemap.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VisitZuidLimburgScraper/3.0; +https://github.com/)",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

REQUEST_DELAY_SECONDS = 0.4  # netjes scrapen

KEYWORDS = [
    "direct boeken", "boek nu", "boeken", "reserveren", "naar de website", "website", "booking"
]

DATA_ATTRS = [
    "data-href", "data-url", "data-link", "data-booking-url", "data-booking",
    "data-external-url", "data-cta-url", "data-target-url"
]

def is_external(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return host and "visitzuidlimburg.nl" not in host
    except Exception:
        return False

def classify(url: str) -> str:
    if "/overnachten/hotels/detail/" in url:
        return "hotel-detail"
    if "/overnachten/bed-breakfasts/detail/" in url:
        return "bb-detail"
    if "/overnachten/" in url and "/detail/" in url:
        return "overnachten-detail"
    if "/overnachten/" in url:
        return "overnachten"
    if "/eten-drinken/" in url:
        return "eten-drinken"
    if "/doen/" in url:
        return "doen"
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
    if last.isdigit() and len(parts) >= 2:
        slug = parts[-2]
    else:
        slug = last
    return " ".join(w.upper() if w.lower() in {"bb","b&b","benb"} else w.capitalize()
                    for w in slug.replace("-", " ").split())

def fetch(url: str, session: requests.Session, timeout: int = 30) -> str:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_sitemap(xml_text: str) -> list[tuple[str, str]]:
    blocks = re.findall(r"<url>(.*?)</url>", xml_text, flags=re.DOTALL | re.IGNORECASE)
    out = []
    for b in blocks:
        loc_m = re.search(r"<loc>(.*?)</loc>", b, flags=re.IGNORECASE)
        if not loc_m:
            continue
        loc = loc_m.group(1).strip()
        last_m = re.search(r"<lastmod>(.*?)</lastmod>", b, flags=re.IGNORECASE)
        lastmod = last_m.group(1).strip() if last_m else ""
        out.append((loc, lastmod))
    return out

def extract_outbound_from_soup(soup: BeautifulSoup, base_url: str) -> tuple[str, str]:
    # 1) Normale <a href> met relevante tekst
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ", strip=True) or "").strip().lower()
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        if any(k in text for k in KEYWORDS) and is_external(abs_url):
            return (a.get_text(" ", strip=True), abs_url)

    # 2) data-attributes (vaak bij knoppen)
    for tag in soup.find_all(True):
        text = (tag.get_text(" ", strip=True) or "").strip().lower()
        if not any(k in text for k in KEYWORDS):
            continue
        for attr in DATA_ATTRS:
            v = tag.get(attr)
            if v:
                abs_url = urljoin(base_url, str(v).strip())
                if is_external(abs_url):
                    return (tag.get_text(" ", strip=True), abs_url)

    # 3) onclick="window.open('https://...')"
    for tag in soup.find_all(True):
        text = (tag.get_text(" ", strip=True) or "").strip().lower()
        onclick = (tag.get("onclick") or "").strip()
        if not onclick:
            continue
        if any(k in text for k in KEYWORDS) or any(k in onclick.lower() for k in KEYWORDS):
            m = re.search(r"(https?://[^\s'\"\\)]+)", onclick)
            if m and is_external(m.group(1)):
                return (tag.get_text(" ", strip=True) or "onclick", m.group(1))

    # 4) JSONison: kijk of er ergens een externe url “verstopt” zit
    for script in soup.find_all("script"):
        s = (script.get_text(" ", strip=True) or "")
        m = re.search(r"https?://(?!www\.visitzuidlimburg\.nl)[^\s\"']+", s)
        if m:
            return ("script-url", m.group(0))

    return ("", "")

def extract_outbound(html: str, base_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    return extract_outbound_from_soup(soup, base_url)

def fetch_rendered_html(url: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(800)
        html = page.content()
        browser.close()
        return html

def should_enrich(url: str) -> bool:
    # U wilt alle records in CSV, maar “enrich” alleen waar het logisch is (scheelt enorm veel calls)
    return ("/detail/" in url) or ("/overnachten/" in url)

@dataclass
class Row:
    url: str
    lastmod: str
    type: str
    naam: str
    outbound_label: str
    outbound_url: str
    outbound_source: str

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sitemap_all_enriched_js.csv")
    ap.add_argument("--enrich", action="store_true")
    ap.add_argument("--js", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    s = requests.Session()

    print("Sitemap ophalen…")
    xml = fetch(SITEMAP_URL, s)
    entries = parse_sitemap(xml)
    if args.limit and args.limit > 0:
        entries = entries[:args.limit]

    print(f"Aantal sitemap records: {len(entries)}")
    rows: list[Row] = []

    for i, (url, lastmod) in enumerate(entries, start=1):
        t = classify(url)
        naam = name_from_url(url)

        label = ""
        outurl = ""
        source = "none"

        if args.enrich and should_enrich(url):
            try:
                html = fetch(url, s)
                label, outurl = extract_outbound(html, url)
                source = "html" if outurl else "none"

                if (not outurl) and args.js:
                    rh = fetch_rendered_html(url)
                    label, outurl = extract_outbound(rh, url)
                    source = "rendered" if outurl else "none"
            except Exception as e:
                label = "ERROR"
                outurl = f"{type(e).__name__}: {e}"
                source = "error"

            time.sleep(REQUEST_DELAY_SECONDS)

        if i % 100 == 0:
            print(f"… verwerkt: {i}/{len(entries)}")

        rows.append(Row(url, lastmod, t, naam, label, outurl, source))

    print(f"CSV schrijven: {args.out}")
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["url","lastmod","type","naam_afgeleid","outbound_label","outbound_url","outbound_source"])
        for r in rows:
            w.writerow([r.url, r.lastmod, r.type, r.naam, r.outbound_label, r.outbound_url, r.outbound_source])

    print("Klaar.")

if __name__ == "__main__":
    main()
