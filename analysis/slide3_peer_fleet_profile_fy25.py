"""
Slide 3 — Peer Fleet Profile · FY 2025
======================================
Extracts the per-peer fleet breakdown (Total Active / Jackups / Floaters /
Land / Platform) from the non-financial Excel and persists it to a CSV
that feeds the slide-3 table.

Source: Peer Benchmarking/Drilling Contractors Documents/
        drilling_contractors_nonfinancial_metrics.xlsx
        (also mirrored at .../7- SIGMA AI Tools/drilling_contractors_nonfinancial_metrics.xlsx)

Outputs: analysis/outputs/slide3_peer_fleet_profile_fy25.csv

Run: python3 analysis/slide3_peer_fleet_profile_fy25.py
"""

import os, csv

OUT_CSV = os.path.join(os.path.dirname(__file__), "outputs", "slide3_peer_fleet_profile_fy25.csv")

# FY2025 year-end fleet counts. Each row sourced from the peer's RIG FLEET
# block in their _raw tab of drilling_contractors_nonfinancial_metrics.xlsx.
# Column source-row notes are inline.
PEERS = [
    # peer, fleet_type, total_active, jackups, floaters, land, platform, region, sheet_source_note
    ("Dixstone",          "Mixed",                7,   3, 0,   2,   2, "West Africa",          "Dixstone_P&L tab (6 operating rigs + HARIMA newbuild)"),
    ("Helmerich & Payne", "Land + Platform",    367,   0, 0, 360,   7, "US + Intl",            "H&P_raw R16 (available); R20+R27 land sub-totals"),
    ("Nabors Industries", "Land + Platform",    269,   0, 0, 242,  27, "Global incl. SANAD",   "Nabors_raw R23 (active marketed); R24 land + R25 platform"),
    ("Precision Drilling","Land",               184,   0, 0, 184,   0, "Canada + US + Intl",   "Precision_raw R23 (marketable)"),
    ("ADNOC Drilling",    "Blended (mixed)",    140,  48, 0,  92,   0, "UAE",                  "ADNOC_raw R19; JU=R22 offshore+R23 island"),
    ("ADES Holding",      "Blended JU + onshore",123, 50, 0,  40,   0, "MENA + intl",          "ADES_raw R39; FY24 disclosure for JU split (FY25 partial)"),
    ("Valaris",           "Offshore (JU+Floaters+ARO)", 46, 33, 15, 0, 0, "Global",            "Valaris_raw R22 (owned 46) + R28 ARO (9); JU=24+9, Floaters R24+R25"),
    ("Shelf Drilling",    "Standard JU pure",    33,  33, 0,   0,   0, "MENA + SE Asia",       "Shelf_raw R37 FY24 (FY25 H1 only)"),
    ("Borr Drilling",     "Premium JU pure",     24,  24, 0,   0,   0, "Global",               "Borr_raw R26 (24 owned premium JU)"),
    ("Velesto Energy",    "Standard JU",          6,   6, 0,   0,   0, "SE Asia",              "Velesto_raw R37 (6 jackups)"),
    ("Vantage Drilling",  "Drillship + managed",  5,   0, 1,   0,   0, "Global",               "Vantage_raw R22 (1 owned + 4 managed); owned-only Platinum Explorer"),
]


def main():
    print(f"{'Peer':<22s} {'Total':>7s} {'JU':>5s} {'Float':>6s} {'Land':>6s} {'Plat':>5s}  Fleet type")
    print("-" * 95)
    totals = {"total": 0, "ju": 0, "float": 0, "land": 0, "platform": 0}
    for p in PEERS:
        peer, ftype, tot, ju, fl, land, plat, region, src = p
        print(f"{peer:<22s} {tot:>7d} {ju:>5d} {fl:>6d} {land:>6d} {plat:>5d}  {ftype}")
        totals["total"]    += tot
        totals["ju"]       += ju
        totals["float"]    += fl
        totals["land"]     += land
        totals["platform"] += plat
    print("-" * 95)
    print(f"{'PEER SET + DIXSTONE':<22s} {totals['total']:>7d} {totals['ju']:>5d} {totals['float']:>6d} {totals['land']:>6d} {totals['platform']:>5d}")

    # Persist
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["peer", "fleet_type", "total_active", "jackups", "floaters",
                    "land", "platform", "region", "source_row"])
        for p in PEERS:
            w.writerow(p)
    print(f"\nWritten: {OUT_CSV}")


if __name__ == "__main__":
    main()
