#!/usr/bin/env python3
"""Genereer data/cpi.json uit het officiele Transparency International CPI-bestand.

De CPI-pagina (transparency.org/en/cpi/<jaar>) laadt de scores via JavaScript en
is dus niet te scrapen. TI publiceert de volledige uitslag wel als downloadbaar
bestand ("CPI<jaar>_Results.zip", met daarin een .xlsx). Dit script leest dat
bestand met de standaardbibliotheek (geen externe dependencies) en schrijft een
schone data/cpi.json die monitor.py inleest.

Eenmaal per jaar draaien wanneer de nieuwe editie verschijnt:

    python tools/cpi_from_xlsx.py CPI2025_Results.zip --editie 2025 \
        --bijgewerkt-per 2026-02-10

Het pad mag de .zip of de .xlsx zelf zijn. Controleer de output (aantal landen,
top/bottom) altijd even met het oog voordat je commit.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
BRON_URL_TMPL = "https://www.transparency.org/en/cpi/{editie}"


def _col_index(cel_ref: str) -> int:
    """'C5' -> 2 (0-based kolomindex)."""
    letters = re.match(r"[A-Z]+", cel_ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _xlsx_bytes(pad: Path) -> bytes:
    """Geef de xlsx-bytes terug, of het nu een .xlsx of een omhullende .zip is."""
    data = pad.read_bytes()
    if pad.suffix.lower() == ".xlsx":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        namen = [n for n in zf.namelist() if n.lower().endswith(".xlsx")]
        if not namen:
            sys.exit(f"Geen .xlsx gevonden in {pad}")
        return zf.read(namen[0])


def _lees_sheet(xlsx: bytes) -> list[list[str]]:
    """Parse het eerste werkblad naar een lijst van rijen (lijsten van celtekst)."""
    with zipfile.ZipFile(io.BytesIO(xlsx)) as zf:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst.findall("m:si", NS):
                strings.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    rijen: list[list[str]] = []
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        cellen: dict[int, str] = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            tekst = "" if v is None else v.text or ""
            if c.get("t") == "s" and tekst != "":
                tekst = strings[int(tekst)]
            elif c.get("t") == "inlineStr":
                is_t = c.find("m:is/m:t", NS)
                tekst = is_t.text or "" if is_t is not None else ""
            cellen[_col_index(c.get("r"))] = tekst
        breedte = (max(cellen) + 1) if cellen else 0
        rijen.append([cellen.get(i, "") for i in range(breedte)])
    return rijen


def _vind_kolommen(rijen: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Zoek de headerrij en map: naam/iso3/regio/score/rang -> kolomindex."""
    for idx, rij in enumerate(rijen):
        laag = [c.strip().lower() for c in rij]
        if any(c.startswith("country") for c in laag) and "iso3" in laag:
            kol = {}
            for i, c in enumerate(laag):
                if c.startswith("country"):
                    kol["naam"] = i
                elif c == "iso3":
                    kol["iso3"] = i
                elif c == "region":
                    kol["regio"] = i
                elif "score" in c and "cpi" in c:
                    kol["score"] = i
                elif c == "rank":
                    kol["rang"] = i
            if {"naam", "iso3", "score", "rang"} <= set(kol):
                return idx, kol
    sys.exit("Kon de headerrij (Country/ISO3/score/Rank) niet vinden.")


def main() -> None:
    p = argparse.ArgumentParser(description="Genereer data/cpi.json uit het CPI-bestand.")
    p.add_argument("bestand", type=Path, help="pad naar CPI<jaar>_Results.zip of .xlsx")
    p.add_argument("--editie", type=int, help="editiejaar (default: uit score-kolom)")
    p.add_argument("--bijgewerkt-per", help="publicatiedatum ISO, bijv. 2026-02-10")
    p.add_argument("--uit", type=Path, default=Path("data/cpi.json"))
    args = p.parse_args()

    rijen = _lees_sheet(_xlsx_bytes(args.bestand))
    kop_idx, kol = _vind_kolommen(rijen)

    editie = args.editie
    if not editie:
        m = re.search(r"(\d{4})", rijen[kop_idx][kol["score"]])
        editie = int(m.group(1)) if m else 0

    landen = []
    for rij in rijen[kop_idx + 1:]:
        if max(kol.values()) >= len(rij):
            continue
        iso = rij[kol["iso3"]].strip()
        score = rij[kol["score"]].strip()
        if not re.fullmatch(r"[A-Z]{3}", iso) or not re.fullmatch(r"\d+(\.\d+)?", score):
            continue
        landen.append({
            "naam": rij[kol["naam"]].strip(),
            "iso3": iso,
            "regio": rij[kol["regio"]].strip() if "regio" in kol else "",
            "score": round(float(score)),
            "rang": int(float(rij[kol["rang"]])),
        })
    landen.sort(key=lambda x: (x["rang"], x["naam"]))
    if not landen:
        sys.exit("Geen landen geparsed — controleer het bestand.")

    obj = {
        "editie": editie,
        "bijgewerkt_per": args.bijgewerkt_per,
        "bron_url": BRON_URL_TMPL.format(editie=editie),
        "databestand": str(args.bestand.name),
        "schaal": "0 (zeer corrupt) tot 100 (zeer schoon)",
        "wereldgemiddelde": round(statistics.mean(l["score"] for l in landen)),
        "landen": landen,
    }
    args.uit.parent.mkdir(parents=True, exist_ok=True)
    args.uit.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.uit}: {len(landen)} landen, editie {editie}, "
          f"wereldgemiddelde {obj['wereldgemiddelde']}.")
    print(f"  top: {landen[0]['naam']} ({landen[0]['score']}) / "
          f"bottom: {landen[-1]['naam']} ({landen[-1]['score']})")


if __name__ == "__main__":
    main()
