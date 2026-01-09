import re
import time
import csv
from dataclasses import dataclass
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.visitzuidlimburg.nl/sitemap.xml"
HOTEL_DETAIL_MARKER = "/overnachten/hotels/detail/"
OUT_CSV = "visitzuidlimburg_hotels_direct_links.csv"

# Netjes scrapen: kleine pauze tussen requests
REQUEST_DELAY_SECONDS = 1.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VisitZuidLimburgScraper/1.0; +https://github.com/)",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

@dataclass
class HotelItem:
    name: str
    detail_url: str
    lastmod: str
    link_label: str
    direct_url: str

def slug_to_name(detail_url: str) -> str:
    parts = [p for p in detail_url.split("/") if p]
    if not parts:
        return ""
    # .../detail/<slug>/<id>/
    # id is meestal numeriek, slug staat ervoor
    slug = parts[-2] if parts[-1].isdigit() else parts[-1]
    name = slug.replace("-", " ").strip()
    # eenvoudige title-case, met behoud van B&B-achtige tokens
    tokens = []
    for w in name.split():
        lw = w.lower()
        if lw in {"b&b", "benb", "bb"}:
            tokens.append("B&B")
        else:
            tokens.append(w.capitalize())
    return " ".join(tokens)

def fetch(url: str, session: requests.Session, timeout: int = 30) -> str:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_sitemap(xml_text: str) -> list[tuple[str, str]]:
    # Heel simpele sitemap-parse zonder extra libs
    # We zoeken <loc>...</loc> en optioneel <lastmod>...</lastmod> in dezelfde <url> block
    url_blocks = re.findall(r"<url>(.*?)</url>", xml_text, flags=re.DOTALL)
    results = []
    for block in url_blocks:
        loc_m = re.search(r"<loc>(.*?)</loc>", block)
        if not loc_m:
            continue
        loc = loc_m.group(1).strip()
        last_m = re.search(r"<lastmod>(.*?)</lastmod>", block)
        lastmod = last_m.group(1).strip() if last_m else ""
        results.append((loc, lastmod))
    return results

def find_booking_or_website_link(html: str, base_url: str) -> tuple[str, str]:
    """
    Probeert in de pagina de knop/link te vinden met tekst:
    - "Direct boeken"
    - "Naar de website"
    Als niets gevonden: ("", "")
    """
    soup = BeautifulSoup(html, "lxml")

    # Zoek alle <a> tags met zichtbare tekst
    candidates = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ", strip=True) or "").strip()
        if not text:
            continue
        candidates.append((text.lower(), text, a["href"]))

    # Prioriteit: Direct boeken, daarna Naar de website
    priority_phrases = [
        ("direct boeken", "Direct boeken"),
        ("naar de website", "Naar de website"),
        ("boek", "Boeken"),  # fallback (soms “Boek nu”)
    ]

    for needle, label in priority_phrases:
        for text_lc, text_orig, href in candidates:
            if needle in text_lc:
                direct = urljoin(base_url, href)
                # label liever de echte knoptekst (als die duidelijk is)
                label_out = text_orig if len(text_orig) <= 40 else label
                return label_out, direct

    return "", ""

def main():
    session = requests.Session()

    print("Sitemap ophalen…")
    sitemap_xml = fetch(SITEMAP_URL, session)

    entries = parse_sitemap(sitemap_xml)
    hotel_entries = [(u, lm) for (u, lm) in entries if HOTEL_DETAIL_MARKER in u]

    print(f"Gevonden hotel-detailpagina's: {len(hotel_entries)}")

    items: list[HotelItem] = []

    for idx, (detail_url, lastmod) in enumerate(hotel_entries, start=1):
        name = slug_to_name(detail_url)
        print(f"[{idx}/{len(hotel_entries)}] {name} -> {detail_url}")

        try:
            html = fetch(detail_url, session)
            link_label, direct_url = find_booking_or_website_link(html, detail_url)
        except Exception as e:
            link_label, direct_url = "ERROR", f"{type(e).__name__}: {e}"

        items.append(
            HotelItem(
                name=name,
                detail_url=detail_url,
                lastmod=lastmod,
                link_label=link_label,
                direct_url=direct_url,
            )
        )

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"CSV schrijven: {OUT_CSV}")

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "detail_url", "lastmod", "link_label", "direct_url"])
        for it in items:
            w.writerow([it.name, it.detail_url, it.lastmod, it.link_label, it.direct_url])

    print("Klaar.")

if __name__ == "__main__":
    main()
