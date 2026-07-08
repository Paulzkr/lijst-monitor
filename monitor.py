#!/usr/bin/env python3
"""
Monitor voor internationale risicolanden-lijsten (Wwft/sancties).

Parseert de bronpagina's structureel naar landenlijsten, vergelijkt op
landniveau met de vorige run (state.json) en annoteert elke wijziging met
NIEUW/VERWIJDERD-badges die 90 dagen zichtbaar blijven. Bij een wijziging
wordt changes.md geschreven (GitHub-issue) en een regel toegevoegd aan
data/historie.json.

Bronnen:
  - EU AML-hoogrisicolanden (DG FISMA): tabel land + datum inwerkingtreding,
    plus de sectie "Evolution of the list".
  - EU fiscale lijst (DG TAXUD): Bijlage I en II als aparte lijsten, plus de
    "Evolution of the EU List" (datum + PDF).
  - EU-sancties (DG FISMA): de "Latest update"-nieuwsregels uit de ticker.
  - FATF: geen automatische bron (bot-blokkade) — uit data/fatf.json.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")
CHANGES_FILE = Path("changes.md")
FATF_FILE = Path("data/fatf.json")
HISTORIE_FILE = Path("data/historie.json")

# Hoe lang NIEUW/VERWIJDERD-badges zichtbaar blijven na de mutatiedatum.
WINDOW_DAGEN = 90

VANDAAG = date.today()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; lijst-monitor/2.0; "
        "interne compliance-monitoring)"
    )
}

EU_AML_URL = (
    "https://finance.ec.europa.eu/financial-crime/"
    "anti-money-laundering-and-countering-financing-terrorism-"
    "international-level_en"
)
EU_TAX_URL = (
    "https://taxation-customs.ec.europa.eu/taxation/"
    "common-eu-list-third-country-jurisdictions-tax-purposes_en"
)
EU_SANCTIES_URL = (
    "https://finance.ec.europa.eu/eu-and-world/"
    "sanctions-restrictive-measures/"
    "overview-sanctions-and-related-resources_en"
)
FATF_URL = "https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html"
FISMA_BASIS = "https://finance.ec.europa.eu"

# Nette namen voor issue/historie.
BRON_NAAM = {
    "eu_aml": "EU AML-hoogrisicolanden (DG FISMA)",
    "eu_tax": "EU fiscale lijst niet-coöperatieve jurisdicties (DG TAXUD)",
    "eu_sancties": "EU-sancties nieuwsoverzicht (DG FISMA)",
    "fatf": "FATF zwarte/grijze lijst",
}


class StructuurError(Exception):
    """De verwachte paginastructuur is niet gevonden — niet vals-leeg maken."""


def sleutel(naam: str) -> str:
    """Normaliseer een landnaam voor vergelijking (curly-apostrof, spaties)."""
    s = (naam or "").replace("’", "'").replace("‘", "'") \
        .replace("ʼ", "'").replace("′", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


# --------------------------------------------------------------------------- #
#  Ophalen
# --------------------------------------------------------------------------- #
def haal_pagina(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def platte_tekst(soup: BeautifulSoup) -> str:
    kopie = BeautifulSoup(str(soup), "html.parser")
    for tag in kopie(["script", "style"]):
        tag.decompose()
    return re.sub(r"\s+", " ", kopie.get_text(" ")).strip()


def absolute_link(href: str | None) -> str | None:
    if href and href.startswith("/"):
        return FISMA_BASIS + href
    return href


def eurlex_link(tekst: str) -> str | None:
    """Bouw een EUR-Lex CELEX-link uit een verordeningsnummer in de tekst."""
    m = re.search(r"\(EU\)\s*(?:No\s*)?(\d{4})/(\d{1,4})", tekst)
    if m:
        jaar, nummer = m.group(1), m.group(2)
    else:
        m2 = re.search(r"\b(\d{3,4})/(\d{4})\b", tekst)  # oud formaat 1675/2016
        if not m2:
            return None
        nummer, jaar = m2.group(1), m2.group(2)
    return (
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:"
        f"3{jaar}R{int(nummer):04d}"
    )


# --------------------------------------------------------------------------- #
#  Parsers per bron  (elke parser geeft een dict of gooit StructuurError)
# --------------------------------------------------------------------------- #
DATUM_RE = re.compile(
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}"
)


def parse_eu_aml(soup: BeautifulSoup) -> dict:
    """Landentabel (land + datum inwerkingtreding) + Evolution-sectie."""
    landen: list[dict] = []
    for tabel in soup.find_all("table"):
        koptekst = " ".join(th.get_text(" ", strip=True).lower()
                            for th in tabel.find_all(["th", "td"])[:4])
        if "entry into force" not in koptekst and "inwerkingtreding" not in koptekst:
            continue
        for rij in tabel.find_all("tr"):
            cellen = rij.find_all(["td", "th"])
            if len(cellen) < 2:
                continue
            naam = cellen[0].get_text(" ", strip=True)
            datum = cellen[1].get_text(" ", strip=True)
            if not naam or "entry into force" in datum.lower() \
                    or not DATUM_RE.search(datum):
                continue
            landen.append({"naam": naam, "datum_inwerkingtreding": datum})
        if landen:
            break

    if not landen:
        raise StructuurError("landentabel EU AML niet gevonden")

    return {"landen": landen, "officiele_historie": parse_evolution_aml(soup)}


def parse_evolution_aml(soup: BeautifulSoup) -> list[dict] | None:
    """Sectie 'Evolution of the list' -> [{datum, naam, link}] of None."""
    kop = soup.find(lambda t: t.name in ("h2", "h3", "h4")
                    and "evolution" in t.get_text(strip=True).lower())
    if not kop:
        return None
    entries: list[dict] = []
    for zib in kop.find_all_next(["li", "p"], limit=40):
        tekst = re.sub(r"\s+", " ", zib.get_text(" ", strip=True)).strip()
        m = DATUM_RE.search(tekst)
        if not m:
            continue
        # verwijder de leidende datum uit de naam en kap onredelijk lange blobs af
        naam = DATUM_RE.sub("", tekst, count=1).strip(" .-–—")
        naam = re.sub(r"\s+", " ", naam)
        if len(naam) > 150:
            naam = naam[:147].rstrip() + "…"
        link_tag = zib.find("a", href=True)
        link = absolute_link(link_tag["href"]) if link_tag else eurlex_link(tekst)
        entries.append({"datum": m.group(0), "naam": naam, "link": link})
    return entries or None


def _split_landen(fragment: str) -> list[str]:
    """'A, B, Turks and Caicos, ... and D' -> lijst.

    Splitst UITSLUITEND op komma's, zodat 'and' binnen een landnaam
    (Turks and Caicos, Trinidad and Tobago) intact blijft. Verwijdert een
    leidend 'and'/'the' (Oxford-komma en lidwoorden).
    """
    schoon = []
    for deel in fragment.split(","):
        deel = re.sub(r"^(?:and|the)\s+", "", deel.strip(" ."),
                      flags=re.IGNORECASE).strip()
        if deel and len(deel) > 1 and not deel.isdigit() \
                and not deel.lower().startswith("jurisdiction"):
            schoon.append(deel)
    return _expand_and(schoon)


# Samengestelde landnamen met 'and' die NIET gesplitst mogen worden.
COMPOUND = {
    "turks and caicos", "trinidad and tobago", "antigua and barbuda",
    "bosnia and herzegovina", "sao tome and principe", "saint kitts and nevis",
    "saint vincent and the grenadines", "wallis and futuna",
    "heard island and mcdonald islands",
}


def _expand_and(namen: list[str]) -> list[str]:
    """Splits een resterend 'X and Y' (laatste twee zonder Oxford-komma),
    maar houdt bekende samengestelde namen (Turks and Caicos) intact."""
    uit: list[str] = []
    for naam in namen:
        if naam.lower() in COMPOUND or " and " not in f" {naam.lower()} ":
            uit.append(naam)
            continue
        for deel in re.split(r"\s+and\s+", naam, flags=re.IGNORECASE):
            deel = deel.strip()
            if deel:
                uit.append(deel)
    return uit


def parse_eu_tax(soup: BeautifulSoup) -> dict:
    """Bijlage I en II + 'Evolution of the EU List' (datum + PDF)."""
    tekst = platte_tekst(soup)

    m1 = re.search(
        r"Annex\s+I\b.*?jurisdiction[s]?\s*[:\-]?\s*(.+?)\s+Annex\s+II",
        tekst, flags=re.IGNORECASE | re.DOTALL)
    m2 = re.search(
        r"Annex\s+II\b.*?jurisdiction[s]?\s*[:\-]?\s*(.+?)"
        r"(?:Evolution|Objectives|Background|$)",
        tekst, flags=re.IGNORECASE | re.DOTALL)
    if not m1 or not m2:
        raise StructuurError("Bijlage I/II van de EU-fiscale lijst niet gevonden")

    bijlage_1 = _split_landen(m1.group(1))
    bijlage_2 = _split_landen(m2.group(1))
    if not bijlage_1 or not bijlage_2:
        raise StructuurError("Bijlagen leeg — structuur mogelijk gewijzigd")

    officiele_historie = None
    kop = soup.find(lambda t: t.name in ("h2", "h3", "h4")
                    and "evolution of the eu list" in t.get_text(strip=True).lower())
    if kop:
        pdf = kop.find_next("a", href=re.compile(r"\.pdf", re.IGNORECASE))
        volg = kop.find_next(["p", "li"])
        blok = kop.get_text(" ", strip=True) + " " + \
            (volg.get_text(" ", strip=True) if volg else "")
        link = absolute_link(pdf["href"]) if pdf is not None else None
        m = DATUM_RE.search(blok)
        datum = m.group(0) if m else None
        if not datum:  # val terug op dd-mm-jjjj uit tekst of bestandsnaam
            md = re.search(r"(\d{2})-(\d{2})-(\d{4})", f"{blok} {link or ''}")
            if md:
                datum = f"{md.group(3)}-{md.group(2)}-{md.group(1)}"
        if pdf is not None or datum:
            officiele_historie = [{"datum": datum, "link": link}]

    return {"bijlage_1": bijlage_1, "bijlage_2": bijlage_2,
            "officiele_historie": officiele_historie}


MAAND_NR = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def naar_iso(datum_eng: str) -> str:
    """'15 June 2026' -> '2026-06-15'; bij twijfel: teruggeven zoals-is."""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", (datum_eng or "").strip())
    if not m:
        return datum_eng
    dag, maand, jaar = int(m.group(1)), MAAND_NR.get(m.group(2).lower()), m.group(3)
    if not maand:
        return datum_eng
    return f"{jaar}-{maand:02d}-{dag:02d}"


def parse_eu_sancties(soup: BeautifulSoup) -> dict:
    """De 'Latest update'-nieuwsregels uit de ticker -> [{datum, tekst, link}]."""
    tekst = platte_tekst(soup)
    updates: list[dict] = []
    patroon = re.compile(
        r"Latest update:?\s*(" + DATUM_RE.pattern + r")\s*[-–]\s*(.+?)"
        r"(?=Latest update|Previous news|Play news|What are sanctions|$)",
        flags=re.IGNORECASE)
    for m in patroon.finditer(tekst):
        omschrijving = m.group(2).strip(" .")[:200]
        if omschrijving:
            updates.append({"datum": naar_iso(m.group(1)),
                            "tekst": omschrijving, "link": None})

    if not updates:
        raise StructuurError("geen 'Latest update'-regels gevonden")

    for upd in updates:
        a = soup.find("a", string=re.compile(re.escape(upd["tekst"][:30]),
                                              re.IGNORECASE))
        if a and a.get("href"):
            upd["link"] = absolute_link(a["href"])
    return {"updates": updates}


def lees_fatf() -> dict:
    """FATF-lijsten uit het handmatige data/fatf.json."""
    if not FATF_FILE.exists():
        raise StructuurError("data/fatf.json ontbreekt")
    data = json.loads(FATF_FILE.read_text(encoding="utf-8"))
    return {
        "bijgewerkt_per": data.get("bijgewerkt_per"),
        "zwarte_namen": data.get("zwarte_lijst", []),
        "grijze_namen": data.get("grijze_lijst", []),
    }


# --------------------------------------------------------------------------- #
#  Annotatielogica (NIEUW / VERWIJDERD, 90-dagen-venster)
# --------------------------------------------------------------------------- #
def _verlopen(mutatiedatum: str | None) -> bool:
    if not mutatiedatum:
        return False
    try:
        return (VANDAAG - date.fromisoformat(mutatiedatum)).days > WINDOW_DAGEN
    except ValueError:
        return False


def verwerk_lijst(oude_entries: list[dict], nieuwe_namen: list[str],
                  extra: dict[str, dict] | None = None,
                  datum_map: dict[str, str] | None = None
                  ) -> tuple[list[dict], list[str], list[str]]:
    """
    Vergelijkt de nieuwe bronlijst met de opgeslagen stand en annoteert.

    Vergelijking gebeurt op een genormaliseerde sleutel (apostrof/spaties),
    zodat een cosmetische spellingswijziging geen valse mutatie oplevert.

    - Nieuw land  -> status 'nieuw', mutatiedatum = detectiedatum (of officiële
      datum uit datum_map, bijv. EU AML datum inwerkingtreding).
    - Verwijderd land -> status 'verwijderd', blijft in de lijst (doorgestreept).
    - Verlopen (>90 dagen): 'nieuw' -> 'actueel' (badge weg); 'verwijderd' wordt
      definitief uit de lijst gehaald. Gebeurt ook zonder nieuwe wijziging.

    Geeft (nieuwe_entries, toegevoegd, verwijderd) terug.
    """
    extra = extra or {}
    datum_map = datum_map or {}

    oude_by_sleutel = {sleutel(e["naam"]): e for e in oude_entries}
    aanwezig_vorig = {sleutel(e["naam"]) for e in oude_entries
                      if e.get("status") in ("actueel", "nieuw")}

    # dedup nieuwe namen op sleutel, volgorde bewaren
    nieuw_paren: list[tuple[str, str]] = []
    gezien = set()
    for naam in nieuwe_namen:
        s = sleutel(naam)
        if s not in gezien:
            gezien.add(s)
            nieuw_paren.append((s, naam))
    nieuw_sleutels = {s for s, _ in nieuw_paren}

    toegevoegd = [naam for s, naam in nieuw_paren if s not in aanwezig_vorig]
    verwijderd = [e["naam"] for e in oude_entries
                  if e.get("status") in ("actueel", "nieuw")
                  and sleutel(e["naam"]) not in nieuw_sleutels]

    toegevoegd_sleutels = {sleutel(n) for n in toegevoegd}
    resultaat: list[dict] = []
    for s, naam in nieuw_paren:
        basis = {"naam": naam, **extra.get(naam, {})}
        if s in toegevoegd_sleutels:
            basis["status"] = "nieuw"
            basis["mutatiedatum"] = datum_map.get(naam, VANDAAG.isoformat())
        else:
            oud = oude_by_sleutel.get(s, {})
            if oud.get("status") == "nieuw" and not _verlopen(oud.get("mutatiedatum")):
                basis["status"] = "nieuw"
                basis["mutatiedatum"] = oud.get("mutatiedatum")
            else:
                basis["status"] = "actueel"
                basis["mutatiedatum"] = None
        resultaat.append(basis)

    # Zojuist verwijderde landen (doorgestreept toevoegen)
    for naam in verwijderd:
        oud = oude_by_sleutel.get(sleutel(naam), {"naam": naam})
        resultaat.append({**{k: v for k, v in oud.items()
                             if k not in ("status", "mutatiedatum")},
                          "naam": naam, "status": "verwijderd",
                          "mutatiedatum": VANDAAG.isoformat()})

    # Eerder verwijderde landen die nog binnen het venster vallen: laten staan
    for e in oude_entries:
        if (e.get("status") == "verwijderd"
                and sleutel(e["naam"]) not in nieuw_sleutels
                and e["naam"] not in verwijderd):
            if not _verlopen(e.get("mutatiedatum")):
                resultaat.append(e)  # blijft doorgestreept

    return _normaliseer(resultaat), toegevoegd, verwijderd


def _normaliseer(entries: list[dict]) -> list[dict]:
    """Ruim verlopen badges op: nieuw->actueel, verwijderd->weg (na 90 dagen)."""
    uit: list[dict] = []
    for e in entries:
        st, md = e.get("status", "actueel"), e.get("mutatiedatum")
        if st == "nieuw" and _verlopen(md):
            uit.append({**e, "status": "actueel", "mutatiedatum": None})
        elif st == "verwijderd" and _verlopen(md):
            continue
        else:
            uit.append(e)
    return uit


# --------------------------------------------------------------------------- #
#  Hoofdlogica
# --------------------------------------------------------------------------- #
def main() -> None:
    oude_state = (json.loads(STATE_FILE.read_text(encoding="utf-8"))
                  if STATE_FILE.exists() else {})
    nieuwe_state: dict = {}
    issue_secties: list[str] = []
    fouten: list[str] = []
    historie_toevoegingen: list[dict] = []
    vandaag_iso = VANDAAG.isoformat()

    def bewaar_oud(bron_id: str) -> None:
        if bron_id in oude_state:
            nieuwe_state[bron_id] = oude_state[bron_id]

    def registreer(bron_id: str, toegevoegd: list[str], verwijderd: list[str],
                   sub_prefix: str = "") -> None:
        if not (toegevoegd or verwijderd):
            return
        naam = BRON_NAAM[bron_id]
        label = f"{naam}{(' — ' + sub_prefix) if sub_prefix else ''}"
        delen = []
        if toegevoegd:
            delen.append("NIEUW " + ", ".join(toegevoegd))
        if verwijderd:
            delen.append("VERWIJDERD " + ", ".join(verwijderd))
        issue_secties.append(f"- **{label}**: " + "; ".join(delen))
        historie_toevoegingen.append({
            "datum": vandaag_iso, "bron_id": bron_id, "bron": naam,
            "toegevoegd": toegevoegd, "verwijderd": verwijderd,
            "notitie": sub_prefix,
        })

    # ---- EU AML ----------------------------------------------------------- #
    try:
        parsed = parse_eu_aml(haal_pagina(EU_AML_URL))
        oud = oude_state.get("eu_aml", {})
        extra = {l["naam"]: {"datum_inwerkingtreding": l["datum_inwerkingtreding"]}
                 for l in parsed["landen"]}
        datum_map = {l["naam"]: naar_iso(l["datum_inwerkingtreding"])
                     for l in parsed["landen"]}
        entries, toe, verw = verwerk_lijst(
            oud.get("landen", []), [l["naam"] for l in parsed["landen"]],
            extra=extra, datum_map=datum_map)
        nieuwe_state["eu_aml"] = {
            "status": "ok", "laatste_check": vandaag_iso,
            "laatste_wijziging": vandaag_iso if (toe or verw)
            else oud.get("laatste_wijziging"),
            "landen": entries,
            "officiele_historie": parsed["officiele_historie"]
            if parsed["officiele_historie"] is not None
            else oud.get("officiele_historie"),
        }
        registreer("eu_aml", toe, verw)
    except StructuurError as exc:
        _markeer_structuur(nieuwe_state, oude_state, "eu_aml", vandaag_iso)
        fouten.append(f"- **{BRON_NAAM['eu_aml']}**: structuur gewijzigd — "
                      f"handmatig controleren ({exc}). {EU_AML_URL}")
    except requests.RequestException as exc:
        bewaar_oud("eu_aml")
        _markeer_check(nieuwe_state, "eu_aml", vandaag_iso, "niet_bereikbaar")
        fouten.append(f"- **{BRON_NAAM['eu_aml']}**: niet bereikbaar "
                      f"({exc.__class__.__name__}). {EU_AML_URL}")

    # ---- EU fiscaal ------------------------------------------------------- #
    try:
        parsed = parse_eu_tax(haal_pagina(EU_TAX_URL))
        oud = oude_state.get("eu_tax", {})
        b1, toe1, verw1 = verwerk_lijst(oud.get("bijlage_1", []), parsed["bijlage_1"])
        b2, toe2, verw2 = verwerk_lijst(oud.get("bijlage_2", []), parsed["bijlage_2"])
        gewijzigd = any([toe1, verw1, toe2, verw2])
        nieuwe_state["eu_tax"] = {
            "status": "ok", "laatste_check": vandaag_iso,
            "laatste_wijziging": vandaag_iso if gewijzigd else oud.get("laatste_wijziging"),
            "bijlage_1": b1, "bijlage_2": b2,
            "officiele_historie": parsed["officiele_historie"]
            if parsed["officiele_historie"] is not None
            else oud.get("officiele_historie"),
        }
        registreer("eu_tax", toe1, verw1, "Bijlage I")
        registreer("eu_tax", toe2, verw2, "Bijlage II")
    except StructuurError as exc:
        _markeer_structuur(nieuwe_state, oude_state, "eu_tax", vandaag_iso)
        fouten.append(f"- **{BRON_NAAM['eu_tax']}**: structuur gewijzigd — "
                      f"handmatig controleren ({exc}). {EU_TAX_URL}")
    except requests.RequestException as exc:
        bewaar_oud("eu_tax")
        _markeer_check(nieuwe_state, "eu_tax", vandaag_iso, "niet_bereikbaar")
        fouten.append(f"- **{BRON_NAAM['eu_tax']}**: niet bereikbaar "
                      f"({exc.__class__.__name__}). {EU_TAX_URL}")

    # ---- EU-sancties ------------------------------------------------------ #
    try:
        parsed = parse_eu_sancties(haal_pagina(EU_SANCTIES_URL))
        oud = oude_state.get("eu_sancties", {})
        oude_datums = {u["datum"] for u in oud.get("updates", [])}
        nieuwe_datums = {u["datum"] for u in parsed["updates"]}
        gewijzigd = bool(oud) and oude_datums != nieuwe_datums
        nieuwe_state["eu_sancties"] = {
            "status": "ok", "laatste_check": vandaag_iso,
            "laatste_wijziging": vandaag_iso if gewijzigd else oud.get("laatste_wijziging"),
            "updates": parsed["updates"],
        }
        if gewijzigd:
            nieuw = [u["tekst"] for u in parsed["updates"]
                     if u["datum"] not in oude_datums]
            issue_secties.append(f"- **{BRON_NAAM['eu_sancties']}**: nieuwe update(s): "
                                 + "; ".join(nieuw))
            historie_toevoegingen.append({
                "datum": vandaag_iso, "bron_id": "eu_sancties",
                "bron": BRON_NAAM["eu_sancties"], "toegevoegd": [], "verwijderd": [],
                "notitie": "; ".join(nieuw),
            })
    except StructuurError as exc:
        _markeer_structuur(nieuwe_state, oude_state, "eu_sancties", vandaag_iso)
        fouten.append(f"- **{BRON_NAAM['eu_sancties']}**: structuur gewijzigd — "
                      f"handmatig controleren ({exc}). {EU_SANCTIES_URL}")
    except requests.RequestException as exc:
        bewaar_oud("eu_sancties")
        _markeer_check(nieuwe_state, "eu_sancties", vandaag_iso, "niet_bereikbaar")
        fouten.append(f"- **{BRON_NAAM['eu_sancties']}**: niet bereikbaar "
                      f"({exc.__class__.__name__}). {EU_SANCTIES_URL}")

    # ---- FATF (handmatig) ------------------------------------------------- #
    try:
        fatf = lees_fatf()
        oud = oude_state.get("fatf", {})
        zwart, tz, vz = verwerk_lijst(oud.get("zwarte_lijst", []), fatf["zwarte_namen"])
        grijs, tg, vg = verwerk_lijst(oud.get("grijze_lijst", []), fatf["grijze_namen"])
        gewijzigd = any([tz, vz, tg, vg])
        nieuwe_state["fatf"] = {
            "status": "ok", "bijgewerkt_per": fatf["bijgewerkt_per"],
            "laatste_check": vandaag_iso,
            "laatste_wijziging": vandaag_iso if gewijzigd else oud.get("laatste_wijziging"),
            "zwarte_lijst": zwart, "grijze_lijst": grijs,
        }
        registreer("fatf", tz, vz, "zwarte lijst")
        registreer("fatf", tg, vg, "grijze lijst")
    except StructuurError as exc:
        bewaar_oud("fatf")
        fouten.append(f"- **{BRON_NAAM['fatf']}**: {exc}. {FATF_URL}")

    # ---- Wegschrijven ----------------------------------------------------- #
    STATE_FILE.write_text(
        json.dumps(nieuwe_state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    if historie_toevoegingen:
        historie = (json.loads(HISTORIE_FILE.read_text(encoding="utf-8"))
                    if HISTORIE_FILE.exists() else [])
        historie.extend(historie_toevoegingen)
        HISTORIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORIE_FILE.write_text(
            json.dumps(historie, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    if issue_secties or fouten:
        kop = f"# Wijzigingen risicolanden-lijsten — {vandaag_iso}\n\n"
        blokken = []
        if issue_secties:
            blokken.append("## Gedetecteerde wijzigingen\n\n" + "\n".join(issue_secties))
        if fouten:
            blokken.append("## Aandachtspunten (bron niet leesbaar)\n\n" + "\n".join(fouten))
        advies = ("\n\n---\n*Tip: plak dit issue in een chat met Claude om de "
                  "wijziging te duiden en het overzichtsdocument bij te werken.*\n")
        CHANGES_FILE.write_text(kop + "\n\n".join(blokken) + advies, encoding="utf-8")
        print("Wijzigingen of aandachtspunten gevonden - changes.md geschreven.")
    else:
        CHANGES_FILE.unlink(missing_ok=True)
        print("Geen wijzigingen.")

    sys.exit(0)


def _markeer_structuur(nieuw: dict, oud: dict, bron_id: str, dt: str) -> None:
    """Behoud de laatst bekende lijst maar zet status op 'structuur_gewijzigd'."""
    basis = dict(oud.get(bron_id, {}))
    basis["status"] = "structuur_gewijzigd"
    basis["laatste_check"] = dt
    nieuw[bron_id] = basis


def _markeer_check(nieuw: dict, bron_id: str, dt: str, status: str) -> None:
    if bron_id in nieuw:
        nieuw[bron_id]["status"] = status
        nieuw[bron_id]["laatste_check"] = dt


if __name__ == "__main__":
    main()
