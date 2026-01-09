import re
import csv
import time
import argparse
from dataclasses import dataclass
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.visitzuidlimburg.nl/sitemap.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VisitZuidLimburgScraper/2.0; +https://github.com/)",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

# netjes scrapen
REQUEST_DELAY_SECONDS = 0.4  # verlaag/verhoog naar wens

# Welke linkteksten zoeken we?
KEYWORDS_PRIORITY = [
    ("direct boeken", "Direct boeken"),
    ("boek nu", "Boek nu"),
    ("boeken", "Boeken"),
    ("naar de website", "Naar de website"),
    ("website", "Website"),
    ("reserver", "Reserveren"),
    ("booking", "Booking"),
]

# Optioneel: alleen deze soorten pagina's "enrichen" (scheelt veel requests)
ENRICH_ONLY_IF_URL_CONTAINS = [
    "/overnachten/",
    "/eten-drinken/",
    "/doen/",
    "/zien-beleven/",
]

@dataclass
class Record:
    url: str
    lastmod: str
    type: str
    naam_afgeleid: str
    outbound_label: str
    outbound_url: str
    outbound_source: str  # html | rendered | none | error


def fetch(url: str, session: requests.Session, timeout: int = 30) -> str:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_sitemap(xml_text: str) -> list[tuple[str, str]]:
    """
    Simpele sitemap-parse: pakt <loc> en <lastmod> uit <url> blocks.
    """
    blocks = re.findall(r"<url>(.*?)</url>", xml_text, flags=re.DOTALL | re.IGNORECASE)
    results = []
    for block in blocks:
        loc_m = re.search(r"<loc>(.*?)</loc>", block, flags=re.IGNORECASE)
        if not loc_m:
            continue
        loc = loc_m.group(1).strip()
        last_m = re.search(r"<lastmod>(.*?)</lastmod>", block, flags=re.IGNORECASE)
        lastmod = last_m.group(1).strip() if last_m else ""
        results.append((loc, lastmod))
    return results


def classify(url: str) -> str:
    # eenvoudige classificatie o.b.v. pad
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
    # vaak eindigt detail URL op .../<slug>/<id>/
    last = parts[-1]
    if last.isdigit() and len(parts) >= 2:
        slug = parts[-2]
    else:
        slug = last
    name = slug.replace("-", " ").strip()
    # milde "title" zonder rare dingen
    return " ".join(w.upper() if w.lower() in {"bb", "b&b", "benb"} else w.capitalize() for w in name.split())


def best_outbound_from_soup(soup: BeautifulSoup, base_url: str) -> tuple[str, str]:
    """
    Zoekt outbound links in <a href="..."> op basis van knop/tekst.
    Retourneert (label, absolute_url) of ("","")
    """
    # alle anchors met tekst
    anchors = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ", strip=True) or "").strip()
        href = (a.get("href") or "").strip()
        if not href:
            continue
        anchors.append((text, href))

    # 1) prioriteit op tekst
    for key, _label in KEYWORDS_PRIORITY:
        key_lc = key.lower()
        for text, href in anchors:
            if text and key_lc in text.lower():
                return text, urljoin(base_url, href)

    # 2) fallback: sommige sites zetten booking url in data-attrs
    for a in soup.find_all("a"):
        text = (a.get_text(" ", strip=True) or "").strip()
        if not text:
            continue
        txt_lc = text.lower()
        if any(k in txt_lc for k, _ in KEYWORDS_PRIORITY):
            for attr in ["data-href", "data-url", "data-link"]:
                v = a.get(attr)
                if v:
                    return text, urljoin(base_url, v)

    # 3) fallback: json-ld (soms staat daar een 'url' in)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            s = script.get_text(strip=True)
            if not s:
                continue
            # heel simpele url extract: we pakken de eerste http(s) url die niet visitzuidlimburg is
            m = re.search(r'https?://(?!www\.visitzuidlimburg\.nl)[^"\s]+', s)
            if m:
                return "JSON-LD url", m.group(0)
        except Exception:
            pass

    return "", ""


def extract_outbound(html: str, base_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    return best_outbound_from_soup(soup, base_url)


def fetch_rendered_html(url: str) -> str:
    """
    Laadt pagina met JS (Playwright) en geeft de gerenderde HTML terug.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)

        # soms blokkeert consent; probeer een paar standaard knoppen
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


def should_enrich(url: str) -> bool:
    return any(x in url for x in ENRICH_ONLY_IF_URL_CONTAINS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="visitzuidlimburg_sitemap_all_enriched.csv")
    ap.add_argument("--enrich", action="store_true", help="probeer outbound link per pagina te vinden")
    ap.add_argument("--js", action="store_true", help="als outbound niet in HTML staat: probeer JS-render (Playwright)")
    ap.add_argument("--limit", type=int, default=0, help="0 = alles; anders max aantal records")
    args = ap.parse_args()

    session = requests.Session()

    print("Sitemap ophalen…")
    sitemap_xml = fetch(SITEMAP_URL, session)
    entries = parse_sitemap(sitemap_xml)

    if args.limit and args.limit > 0:
        entries = entries[: args.limit]

    print(f"Aantal sitemap records: {len(entries)}")

    records: list[Record] = []

    for idx, (url, lastmod) in enumerate(entries, start=1):
        rtype = classify(url)
        naam = name_from_url(url)

        outbound_label = ""
        outbound_url = ""
        outbound_source = "none"

        if args.enrich and should_enrich(url):
            try:
                html = fetch(url, session)
                outbound_label, outbound_url = extract_outbound(html, url)
                outbound_source = "html" if outbound_url else "none"

                if (not outbound_url) and args.js:
                    rendered = fetch_rendered_html(url)
                    outbound_label, outbound_url = extract_outbound(rendered, url)
                    outbound_source = "rendered" if outbound_url else "none"

            except Exception as e:
                outbound_label = "ERROR"
                outbound_url = f"{type(e).__name__}: {e}"
                outbound_source = "error"

            time.sleep(REQUEST_DELAY_SECONDS)

        if idx % 100 == 0:
            print(f"… verwerkt: {idx}/{len(entries)}")

        records.append(
            Record(
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
        w.writerow([
            "url",
            "lastmod",
            "type",
            "naam_afgeleid",
            "outbound_label",
            "outbound_url",
            "outbound_source",
        ])
        for r in records:
            w.writerow([
                r.url,
                r.lastmod,
                r.type,
                r.naam_afgeleid,
                r.outbound_label,
                r.outbound_url,
                r.outbound_source,
            ])

    print("Klaar.")


if __name__ == "__main__":
    main()
