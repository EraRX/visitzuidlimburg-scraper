#!/usr/bin/env python3
# main.py
#
# Leest FIT.csv en schrijft output/data.csv met exact 4 velden:
# naam, plaats, website, categorie
#
# Geen extra velden of functies (AVG-minimaal).
# Filtert ongewenste platform/redirect links (VVV/booking etc.).

import csv
import os
import re
from urllib.parse import urlparse

INPUT_FILE = "FIT.csv"
OUTPUT_FILE = "output/data.csv"

FIELDNAMES_OUT = ["naam", "plaats", "website", "categorie"]


def clean_text(s: str) -> str:
    return (s or "").strip()


def clean_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    # verwijder tracking
    u = re.sub(r"\?.*$", "", u)
    return u.rstrip("/")


def domain(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ""


def is_bad_website(u: str) -> bool:
    if not u:
        return True

    d = domain(u)
    bad_domains = [
        "vvnnederland.nl",
        "visitzuidlimburg.nl",
        "booking.com",
        "hotels.com",
        "expedia.",
        "airbnb.",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "tripadvisor.",
        "thefork.",
        "resengo.",
        "couverts.",
    ]

    for b in bad_domains:
        if b in d:
            return True

    return False


def main():
    # output map maken
    os.makedirs(os.path.dirname(OUTPUT_FILE) or ".", exist_ok=True)

    # FIT.csv lezen (puntkomma)
    with open(INPUT_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)

    out_rows = []
    for r in rows:
        naam = clean_text(r.get("naam_afgeleid") or r.get("name") or r.get("naam") or "")
        plaats = clean_text(r.get("city") or r.get("plaats") or "")
        website = clean_url(r.get("url") or r.get("website") or "")
        categorie = clean_text(r.get("category") or r.get("categorie") or "")

        # Alleen bewaren als we minimaal naam+plaats hebben
        if not naam or not plaats:
            continue

        # Website mag leeg zijn, maar als hij gevuld is: filter redirect/platform
        if website and is_bad_website(website):
            website = ""

        out_rows.append(
            {
                "naam": naam,
                "plaats": plaats,
                "website": website,
                "categorie": categorie,
            }
        )

    # schrijven
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES_OUT)
        w.writeheader()
        w.writerows(out_rows)

    print(f"Klaar. In: {len(rows)} regels, Uit: {len(out_rows)} regels -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
