#!/usr/bin/env python3
"""
Monitor voor internationale risicolanden-lijsten (Wwft/sancties).

Haalt de bronpagina's op, extraheert de relevante sectie, vergelijkt de
hash met de vorige run (state.json) en schrijft bij wijzigingen een
changes.md die door de GitHub Actions-workflow als issue wordt gepost.
"""

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")
CHANGES_FILE = Path("changes.md")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; lijst-monitor/1.0; "
        "interne compliance-monitoring)"
    )
}

# Elke bron: naam, url en twee tekstmarkeringen waartussen de relevante
# sectie ligt. Markeringen ontbreken? Dan wordt de hele paginatekst gehasht.
SOURCES = [
    {
        "id": "eu_aml",
        "naam": "EU AML-hoogrisicolanden (DG FISMA)",
        "url": (
            "https://finance.ec.europa.eu/financial-crime/"
            "anti-money-laundering-and-countering-financing-terrorism-"
            "international-level_en"
        ),
        "start": "Latest version of the list of high-risk third countries",
        "eind": "The listing process",
    },
    {
        "id": "eu_tax",
        "naam": "EU fiscale lijst niet-coöperatieve jurisdicties (DG TAXUD)",
        "url": (
            "https://taxation-customs.ec.europa.eu/taxation/"
            "common-eu-list-third-country-jurisdictions-tax-purposes_en"
        ),
        "start": "The EU List",
        "eind": "Objectives of the EU List",
    },
    {
        "id": "fatf",
        "naam": "FATF zwarte/grijze lijst",
        "url": "https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html",
        "start": None,  # hele pagina hashen
        "eind": None,
    },
    {
        "id": "eu_sancties",
        "naam": "EU-sancties nieuwsoverzicht (DG FISMA)",
        "url": (
            "https://finance.ec.europa.eu/eu-and-world/"
            "sanctions-restrictive-measures/"
            "overview-sanctions-and-related-resources_en"
        ),
        "start": "Latest update",
        "eind": "Sanctions are an essential tool",
    },
]


def haal_sectie_op(bron: dict) -> tuple[str | None, str]:
    """Geeft (sectietekst, status) terug. Sectietekst is None bij fout."""
    try:
        resp = requests.get(bron["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return None, f"niet bereikbaar ({exc.__class__.__name__})"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    tekst = re.sub(r"\s+", " ", soup.get_text(" ")).strip()

    if bron["start"] and bron["start"] in tekst:
        tekst = tekst.split(bron["start"], 1)[1]
    if bron["eind"] and bron["eind"] in tekst:
        tekst = tekst.split(bron["eind"], 1)[0]

    return tekst.strip(), "ok"


def main() -> None:
    oude_state = (
        json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    )
    nieuwe_state: dict = {}
    wijzigingen: list[str] = []
    fouten: list[str] = []

    for bron in SOURCES:
        tekst, status = haal_sectie_op(bron)
        if tekst is None:
            fouten.append(f"- **{bron['naam']}**: {status} — handmatig checken: {bron['url']}")
            # behoud oude state zodat een tijdelijke storing geen vals alarm geeft
            if bron["id"] in oude_state:
                nieuwe_state[bron["id"]] = oude_state[bron["id"]]
            continue

        digest = hashlib.sha256(tekst.encode()).hexdigest()
        fragment = tekst[:600]
        nieuwe_state[bron["id"]] = {"hash": digest, "fragment": fragment}

        oud = oude_state.get(bron["id"])
        if oud and oud["hash"] != digest:
            wijzigingen.append(
                f"## {bron['naam']}\n\n"
                f"Bron: {bron['url']}\n\n"
                f"**Was (fragment):**\n> {oud['fragment']}\n\n"
                f"**Is nu (fragment):**\n> {fragment}\n"
            )

    STATE_FILE.write_text(json.dumps(nieuwe_state, indent=2, ensure_ascii=False))

    if wijzigingen or fouten:
        kop = f"# Wijzigingen risicolanden-lijsten — {date.today().isoformat()}\n\n"
        advies = (
            "\n---\n*Tip: plak dit issue in een chat met Claude met de vraag "
            "om de wijziging te duiden en het overzichtsdocument bij te werken.*\n"
        )
        blokken = wijzigingen + (
            ["## Niet-bereikbare bronnen\n\n" + "\n".join(fouten)] if fouten else []
        )
        CHANGES_FILE.write_text(kop + "\n".join(blokken) + advies)
        print("Wijzigingen of fouten gevonden — changes.md geschreven.")
    else:
        CHANGES_FILE.unlink(missing_ok=True)
        print("Geen wijzigingen.")

    sys.exit(0)


if __name__ == "__main__":
    main()
