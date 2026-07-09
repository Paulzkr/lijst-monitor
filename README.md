# Lijst-monitor — risicolanden & sancties

Wekelijkse monitor op de bronnen uit het overzichtsdocument:

1. EU AML-hoogrisicolanden (DG FISMA)
2. EU fiscale lijst niet-coöperatieve jurisdicties (DG TAXUD)
3. FATF zwarte/grijze lijst
4. EU-sancties nieuwsoverzicht (DG FISMA)
5. Transparency International CPI — volledige corruptie-index (jaarlijks)

Bij een wijziging maakt GitHub automatisch een **issue** aan met een was/is-fragment,
en daarvan krijg je standaard een e-mailnotificatie van GitHub.

## Installatie (± 10 minuten, eenmalig)

1. Maak een (privé) repository aan op GitHub, bijv. `lijst-monitor`.
2. Zet deze drie bestanden erin (mapstructuur behouden):
   - `monitor.py`
   - `.github/workflows/monitor.yml`
   - `README.md`
3. Klaar. De workflow draait elke maandagochtend. Test direct via
   **Actions → Monitor risicolanden-lijsten → Run workflow**.
   De eerste run legt alleen de nulmeting vast (`state.json`); vanaf de
   tweede run worden wijzigingen gemeld.
4. Controleer onder je GitHub-profiel → Settings → Notifications dat
   e-mail voor issues aanstaat (standaard: ja).

## Doorontwikkelen met Claude Code

Open de repo in Claude Code en vraag bijvoorbeeld:

- "Voeg een Slack- of e-mailnotificatie toe naast het GitHub-issue"
- "Parseer de landentabel op de FISMA-pagina en toon in het issue precies
  welke landen zijn toegevoegd of verwijderd" (nu wordt een tekstfragment getoond)
- "Voeg de geconsolideerde sanctielijst (EU Open Data Portal) toe als bron
  en vergelijk het aantal listings"

## Kanttekeningen

- **FATF weert soms geautomatiseerde toegang.** Als de FATF-pagina een
  403-fout geeft, meldt het script dat als "niet bereikbaar" in het issue
  (geen vals alarm, wel een signaal om handmatig te kijken). Vast vangnet:
  de FATF publiceert wijzigingen altijd direct na de plenaires in
  **februari, juni en oktober**.
- **CPI is een jaarlijkse, semi-handmatige bron.** Transparency International
  publiceert de Corruption Perceptions Index één keer per jaar; de scores op de
  website worden via JavaScript geladen en zijn niet te scrapen. Werk daarom bij
  een nieuwe editie `data/cpi.json` bij met het officiële resultaatbestand:
  `python tools/cpi_from_xlsx.py CPI<jaar>_Results.zip --editie <jaar> --bijgewerkt-per <ISO-datum>`.
  De CPI meet *waargenomen* publieke corruptie — een risico-indicatie, geen
  bindende sanctie- of Wwft-listing.
- **Dit is beleidsmonitoring, geen sanctiescreening.** Nieuwe EU-listings
  zijn bindend op het moment van publicatie in het Publicatieblad;
  cliëntscreening moet via een tool die de geconsolideerde lijst dagelijks
  inleest.
- Kleine redactionele aanpassingen aan de webpagina's kunnen een melding
  veroorzaken zonder inhoudelijke wijziging (vals-positief). Het
  was/is-fragment in het issue maakt dat in één oogopslag duidelijk.
- Geen persoonsgegevens: het script verwerkt alleen openbare landenlijsten.
