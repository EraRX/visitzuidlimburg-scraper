import argparse, csv, re, time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.visitzuidlimburg.nl/sitemap.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VisitZuidLimburgScraper/FINAL; +https://github.com/)",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

KEYWORDS = ["direct boeken","boek nu","boeken","reserveren","naar de website","website","booking","reserveer","book"]
DATA_ATTRS = ["data-href","data-url","data-link","data-booking","data-booking-url","data-external-url","data-cta-url","data-target-url","data-redirect","data-redirect-url"]

@dataclass
class Row:
    url: str
    lastmod: str
    type: str
    naam_afgeleid: str
    outbound_label: str
    outbound_url: str
    outbound_source: str  # none|html|rendered|error

def fetch_text(s: requests.Session, url: str, timeout: int) -> str:
    r = s.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_sitemap(xml_text: str):
    root = ET.fromstring(xml_text)
    ns = ""
    m = re.match(r"\{(.*)\}", root.tag)
    if m:
        ns = m.group(1)

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}" if ns else tag

    out = []
    for u in root.findall(q("url")):
        loc = u.find(q("loc"))
        lm  = u.find(q("lastmod"))
        url = (loc.text or "").strip() if loc is not None else ""
        lastmod = (lm.text or "").strip() if lm is not None else ""
        if url:
            out.append((url, lastmod))
    return out

def classify(url: str) -> str:
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
    slug = parts[-2] if last.isdigit() and len(parts) >= 2 else last
    slug = slug.replace("-", " ").strip()
    return " ".join("B&B" if w.lower() in {"bb","b&b","benb"} else w.capitalize() for w in slug.split())

def is_external(u: str) -> bool:
    host = urlparse(u).netloc.lower()
    return bool(host) and "visitzuidlimburg.nl" not in host

def find_outbound(soup: BeautifulSoup, base_url: str):
    # 1) <a href> met knoptekst
    for a in soup.find_all("a", href=True):
        txt = (a.get_text(" ", strip=True) or "").strip()
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        if txt and any(k in txt.lower() for k in KEYWORDS) and is_external(abs_url):
            return (txt, abs_url)

    # 2) data-attribuut met url
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

    # 3) onclick met externe url
    for tag in soup.find_all(True):
        onclick = (tag.get("onclick") or "").strip()
        if not onclick:
            continue
        m = re.search(r"(https?://[^\s'\"\\)]+)", onclick)
        if m and is_external(m.group(1)):
            label = (tag.get_text(" ", strip=True) or "onclick").strip()
            return (label, m.group(1))

    return ("", "")

def extract_outbound(html: str, base_url: str):
    return find_outbound(BeautifulSoup(html, "lxml"), base_url)

def fetch_rendered_html(url: str, timeout_ms: int):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_timeout(800)
        html = page.content()
        b.close()
        return html

def should_enrich(url: str) -> bool:
    # Alle records in CSV, maar extra calls alleen waar kans reëel is
    return ("/detail/" in url) or ("/overnachten/" in url)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sitemap_all_enriched_js.csv")
    ap.add_argument("--enrich", action="store_true")
    ap.add_argument("--js", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--render-timeout", type=int, default=60000)
    args = ap.parse_args()

    print("### RUNNING main.py (NO HOTEL FILTER) ###")

    s = requests.Session()
    xml = fetch_text(s, SITEMAP_URL, timeout=args.timeout)
    entries = parse_sitemap(xml)

    if args.limit and args.limit > 0:
        entries = entries[:args.limit]

    print(f"Aantal sitemap records: {len(entries)}")
    if entries:
        print("Eerste URL:", entries[0][0])

    rows = []
    for i, (url, lastmod) in enumerate(entries, start=1):
        rtype = classify(url)
        naam = name_from_url(url)

        label, outurl, source = "", "", "none"

        if args.enrich and should_enrich(url):
            try:
                html = fetch_text(s, url, timeout=args.timeout)
                label, outurl = extract_outbound(html, url)
                source = "html" if outurl else "none"

                if args.js and (not outurl) and "/detail/" in url:
                    rh = fetch_rendered_html(url, timeout_ms=args.render_timeout)
                    label, outurl = extract_outbound(rh, url)
                    source = "rendered" if outurl else source

            except Exception as e:
                label, outurl, source = "ERROR", f"{type(e).__name__}: {e}", "error"

            time.sleep(max(0.0, args.delay))

        rows.append(Row(url, lastmod, rtype, naam, label, outurl, source))

        if i % 200 == 0:
            print(f"… verwerkt: {i}/{len(entries)}")

    print(f"CSV schrijven: {args.out}")
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["url","lastmod","type","naam_afgeleid","outbound_label","outbound_url","outbound_source"])
        for r in rows:
            w.writerow([r.url, r.lastmod, r.type, r.naam_afgeleid, r.outbound_label, r.outbound_url, r.outbound_source])

    print("Klaar.")

if __name__ == "__main__":
    main()
PY
