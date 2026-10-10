---
title: Taxonomie zur Klassifikation von GitHub-Organisationen
purpose: Kategorien und Abgrenzungsregeln festhalten, die sich aus der manuellen Durchsicht von 1000+ Orgs ergeben haben
status: stabil, basiert auf n=1000 Handklassifikationen
---

# Taxonomie

## Kernauftrag (vom Fachvorgesetzten)

Klassifiziere GitHub-Organisationen nach:
1. **Forschung** — Forschungseinrichtung
2. **Unternehmen** — kommerzielle Firma
3. **Einzelner Contributor** — Einzelperson

## Erweiterte Taxonomie (aus der Praxis entstanden)

Bei der manuellen Durchsicht von über 1000 zufällig gezogenen Organisationen zeigte sich schnell,
dass die drei Kernkategorien einen erheblichen Teil der Population nicht abdecken. Drei weitere
Kategorien wurden ergänzt, weil sie sich klar von den anderen unterscheiden lassen und beim
Fachvorgesetzten explizit als sinnvoll bestätigt wurden (nicht "besonders wichtig", aber es macht
Sinn, sie zu führen, statt sie in eine der drei Kernkategorien zu zwingen):

4. **Behörde** — staatliche/intergouvernementale Einrichtung (z. B. `NOAA-EMC`, `belastingdienst`,
   `internationalidea`)
5. **NGO/Nonprofit** — gemeinnützige Organisation, Stiftung, Verein (z. B. `open-avenues-foundation`,
   `alles-im-rudel` — deutsches "e.V.")
6. **Community/informelles Projekt** — Open-Source-Projekt, Hobby-/Fan-Gruppe, Meetup, ohne
   erkennbaren formalen Rechtsträger (z. B. `xenia-canary`, `beijingpm`, Hackathon-Teams)

Zusätzlich: **Nicht klassifizierbar** — kein ausreichendes Signal in Name/Bio/Blog/Location, um
überhaupt eine Kategorie zu vergeben. Das ist keine siebte inhaltliche Kategorie, sondern eine
Kennzeichnung fehlender Daten (siehe unten, macht ~34% der Gesamtpopulation aus).

## Wichtige Abgrenzungsregeln (empirisch gefunden)

### "Bildung" ist nicht automatisch "Forschung"
Kommerzielle Coding-Bootcamps (`PdxCodeGuild`, `Kenzie-Academy-Brasil`, `itjuana-bootcamp`) sind
**Unternehmen**, keine Forschungseinrichtungen — auch wenn sie unterrichten. Studierendengruppen
und Uni-Kurs-Repos (`ThundeRatz`, `aaaimx`, unzählige `xyz-course-2023`-Repos) sind dagegen
**Forschung/Bildung**, weil sie institutionell an eine Hochschule angebunden sind, auch wenn sie
selbst keine Forschung betreiben. Grenzfall-Kriterium: kommerzieller Anbieter vs. institutionelle
Anbindung (Uni-E-Mail-Domain, `.edu`/`.ac.*`-Link, Kursnummer-Namensmuster).

### Konzern-Unterkonten verzerren jede Org-Zählung
Große Firmen betreiben oft dutzende separate GitHub-Organization-Accounts pro Produkt/Team
(`microsoftgraph`, `SharePoint`, `skills` — alle Microsoft; `RHSecurityCompliance` — Red Hat;
`sephora-sea` — Sephora-Tochter; `lg-uplus-movie-sns-picky` — LG Uplus). Wer "Anzahl Unternehmen"
statt "Anzahl Org-Accounts" berichten will, muss diese über ein Parent-Mapping deduplizieren
(Analogie: GUO-Signal, das `orbis_matching` bereits nutzt).

### `type=Organization` heißt nicht zwangsläufig "keine Einzelperson"
Manche Personen legen einen GitHub-Organization-Account unter ihrem eigenen Namen an
(`Ravi-Joshi-22`, `jelixfon`, `csdojo-defaang` — YouTube-Personenmarke). Erkennbar oft an
Vorname-Nachname-Login-Mustern, persönlichen Gmail-Adressen als einzigem Kontakt, oder einer
LinkedIn-/persönlichen Domain als "Blog"-Feld.

### Generische Wörter sind gefährliche Klassifikations-Signale
Aus der Fehleranalyse des Regel-Prototyps (Details: [rule_based_classifier.md](rule_based_classifier.md)):
- `"ngo"` als bloßer Teilstring trifft `"mangos"`, `"tango"`, `"mongo"`, `"congo"`, `"bingo"` — Wortgrenzen
  sind Pflicht, kein Freifahrtschein.
- `"agency"` trifft Werbeagenturen (`Balcom Agency`) genauso wie Behörden — als alleiniges
  Unterscheidungsmerkmal ungeeignet.
- `"ministry"` trifft kirchliche Bedeutung (`Ministry Centered Technologies` — Kirchen-Software-Firma)
  genauso wie Regierungsministerien.
- Kurze Akronyme (3-4 Buchstaben: `RCN`, `NWS`, `HST`, `OGC`, `code`, `rails`, `sage`) kollidieren
  bei einer Datenbank mit 130K+ Einträgen (ROR) fast garantiert mit unabhängigen, unverwandten
  GitHub-Orgs gleichen Namens — sollten nur mit Gegenprobe (z. B. Land/Region-Abgleich) akzeptiert
  werden, nicht blind.

### Nonprofit-getragene Open-Source-Projekte sind ein Grenzfall zwischen NGO und Community
`ghc-proposals` (getragen von der Haskell Foundation, einer echten US-Nonprofit) oder `django`
(Django Software Foundation) sind formal NGO-getragen, fühlen sich aber wie normale
Community-Projekte an. Aktuelle Heuristik: NGO nur vergeben, wenn die Rechtsform explizit im
Namen/Bio erkennbar ist (Foundation, Stiftung, e.V., ...); sonst Community.

## Signalquellen pro Kategorie

| Kategorie | Stärkste Signale |
|---|---|
| Forschung | `.edu`/`.ac.*`/`.gov`-artige Domain, ROR-Domain-Treffer, Uni-Name/Abkürzung im Login, akademische E-Mail-Domain |
| Behörde | `.gov`-artige Domain, ROR-Typ `government`, explizite Behördennamen ("Ministry of", "City of", "Department of") |
| NGO | Rechtsform-Keywords (Foundation, Stiftung, e.V., Verein), ROR-Typ `nonprofit`, `.ngo`-TLD |
| Unternehmen | Rechtsform-Suffixe (Inc., GmbH, Ltd., LLC, AS, AŞ, Pvt Ltd, CJSC), Produktbeschreibung mit kommerziellem Ton, professionelle Support-E-Mail |
| Community | `github.io`/`vercel.app`/generische Hosting-Domains als "Blog", Hobby-/Fan-Sprache in der Bio, keine Rechtsform erkennbar |
| Individual | Vorname-Nachname-Login-Muster, persönliche Gmail als einziger Kontakt, persönliche Domain |

Für eine automatisierte Regel-Umsetzung dieser Signale siehe
[rule_based_classifier.md](rule_based_classifier.md); für die empirischen Anteile pro Kategorie
siehe [population_estimate.md](population_estimate.md).
