# utils/normalise.py
import re

def clean_url(url):
    url = url.strip()
    url = re.sub(r"\?.*$", "", url)
    return url.rstrip("/")

def normalise_category(cat):
    c = cat.lower()

    if "restaurant" in c:
        return "Restaurant"
    if "cafe" in c:
        return "Café"
    if "hotel" in c or "logies" in c or "bnb" in c:
        return "Logies"
    if "winkel" in c:
        return "Winkel"
    if "wellness" in c or "sauna" in c:
        return "Wellness"
    if "attract" in c or "museum" in c:
        return "Attractie"

    return "Overig"

def normalise_record(r):
    return {
        "naam": r["naam"].strip(),
        "plaats": r["plaats"].strip(),
        "website": clean_url(r["website"]),
        "categorie": normalise_category(r.get("categorie", ""))
    }
