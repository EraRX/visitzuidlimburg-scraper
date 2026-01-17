# sources/fitcsv.py
import csv

def collect(path="FIT.csv"):
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            naam = (row.get("naam_afgeleid") or "").strip()
            plaats = (row.get("city") or "").strip()
            website = (row.get("url") or "").strip()
            categorie = (row.get("category") or "").strip()

            out.append(
                {
                    "naam": naam,
                    "plaats": plaats,
                    "website": website,
                    "categorie": categorie,
                }
            )
    return out
