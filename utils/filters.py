# utils/filters.py

def is_valid(r):
    if not r["naam"]:
        return False
    if not r["plaats"]:
        return False
    if not r["website"]:
        return False

    bad = [
        "vvnnederland.nl",
        "booking.",
        "hotels.com",
        "expedia",
        "reserveer",
        "affiliate",
    ]

    for b in bad:
        if b in r["website"].lower():
            return False

    return True
