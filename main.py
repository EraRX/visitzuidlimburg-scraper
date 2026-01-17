# main.py
#
# Centrale regisseur voor het verzamelen van bedrijfsdata
# Output: naam, plaats, website, categorie
# Geen VVV / booking / affiliate links
# Geen vergelijkingen, geen deduplicatie

from sources import osm, visitzl, eetnu
from utils.normalise import normalise_record
from utils.filters import is_valid
import csv
from config import (
    USE_OSM,
    USE_VISITZL,
    USE_EETNU,
    OUTPUT_FILE
)


def collect_all():
    records = []

    if USE_OSM:
        print("▶ OSM verzamelen...")
        records.extend(osm.collect())

    if USE_VISITZL:
        print("▶ VisitZuidLimburg verzamelen...")
        records.extend(visitzl.collect())

    if USE_EETNU:
        print("▶ Eet.nu verzamelen...")
        records.extend(eetnu.collect())

    return records


def main():
    raw_records = collect_all()
    print(f"Totaal opgehaald (ruw): {len(raw_records)}")

    clean_records = []

    for r in raw_records:
        r = normalise_record(r)
        if is_valid(r):
            clean_records.append(r)

    print(f"Na filtering: {len(clean_records)}")

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["naam", "plaats", "website", "categorie"]
        )
        writer.writeheader()
        writer.writerows(clean_records)

    print(f"✔ Output geschreven naar: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
