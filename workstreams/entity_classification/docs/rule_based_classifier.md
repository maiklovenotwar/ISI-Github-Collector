---
title: Regelbasierter Klassifikator (ROR + Keywords) — Aufbau, Bugs, Ergebnisse
purpose: Dokumentieren, was der erste automatisierte Ansatz kann und wo seine Grenzen liegen
status: Prototyp, lauffähig, nicht produktiv eingesetzt
---

# Regelbasierter Klassifikator

## Ziel und Umfang

Deckt **nur** die Kategorien Forschung, Behörde und NGO ab. Unternehmen/Community/Individual
werden von dieser Regel bewusst **nicht** adressiert — dafür fehlt der Regel jedes Signal (kein
Firmenregister, keine Keyword-Liste, die zuverlässig zwischen "Community-Projekt" und "kleine
Firma" unterscheiden könnte).

## Aufbau (`scripts/classify_organizations_by_rules.py`, `scripts/evaluate_rules_on_goldstandard.py`)

1. **ROR-Domain-Abgleich** (stärkstes Signal): Die `blog`- und `email`-Domain jeder Org wird gegen
   die [ROR-Datenbank](https://ror.org) (132.537 Forschungs-/Förderorganisationen, CC0) abgeglichen.
   ROR-Typ `education`/`facility`/`archive` → Forschung, `government` → Behörde, `nonprofit` → NGO.
   `funder` wird bewusst **nicht** gemappt (siehe Bug-Historie unten).
2. **ROR-Namensabgleich** (Fallback): Nur bei fehlendem Domain-Treffer, nur gegen volle,
   mehrwortige Institutsnamen — **nicht** gegen das Akronym-Feld (siehe Bug-Historie).
3. **Keyword-Heuristik**: Wortgrenzen-sichere (`\b...\b`) Treffer für Forschungs-/Behörden-/
   NGO-typische Begriffe in Name/Bio/Company/Login/Location.

## Bug-Historie (drei Iterationen, jeweils gegen den Goldstandard geprüft)

### Iteration 1: naive Keyword-Liste (kein ROR)
Recall Forschung ~35%, Behörde ~33%, NGO ~0-25%. Basis-Version, viele blinde Flecken bei
Nicht-`.edu`/`.gov`-Domains.

### Iteration 2: + ROR-Domain-Abgleich
Recall verbessert (Forschung 53%, Behörde 67%), aber ein **erster Voll-DB-Lauf** deckte massive
False Positives auf, die im 100er-Goldstandard nicht sichtbar waren (zu kleine Stichprobe für
seltene Kollisionen):

- `rails`, `boost`, `vlc`, `sage`, `code`, `intercom` fälschlich als Forschung/NGO erkannt —
  zufällige Namensgleichheit mit einer unabhängigen ROR-Organisation, kein echter Zusammenhang.
- Bug: das Schlüsselwort `"ngo"` als bloßer Teilstring matchte auch **"mangos", "tango", "mongo",
  "congo", "bingo"**.
- `"ministry"` traf kirchliche Bedeutung (`Ministry Centered Technologies` = Kirchen-Software-Firma).
- `"agency"` traf Werbeagenturen (`Balcom Agency`).
- `typekit` (Adobe-Produkt) wurde fälschlich Forschung zugeordnet, weil Adobe in ROR als
  `funder`-Typ registriert ist (Adobe finanziert Forschungsprojekte, ist selbst aber keine
  Forschungseinrichtung).

### Iteration 3: Fixes
- Wortgrenzen (`\b...\b`) statt Teilstring-Suche für alle Keywords.
- Akronym-Feld aus dem ROR-Namensindex entfernt (nur noch volle, mehrwortige Namen).
- `funder`-Typ aus dem ROR-Bucket-Mapping entfernt.
- Mehrdeutige Keywords entfernt (`ministry`, `agency`, `council`, `state of`, bare `college`).
- Ergebnis: 0 beobachtete False Positives im zweiten Voll-DB-Stichprobencheck, aber messbar
  niedrigerer Recall (Behörde 67%→30%, da `council` als Signal wegfiel).

### Mehrsprachige Erweiterung (getestet, geringer Ertrag)
Spanische/französische/portugiesische/chinesische Keywords wurden ergänzt (`universidad`,
`université`, `研究所`, ...). Effekt auf der Vollmenge: nur **+950 Treffer von 735.242** (+0,13
Prozentpunkte) — deutlich weniger als erwartet. Vermutliche Erklärung: auch nicht-englischsprachige
Institutionen schreiben ihre GitHub-Bio meist auf Englisch.

## Finales Precision/Recall gegen `rule_validation_sample_100.csv`

| Kategorie | Wahrer Anteil (n=100) | Von Regel gefunden | Recall |
|---|---:|---:|---:|
| Forschung | 17 | 9 | ~53% |
| Behörde | 3 | 1 | ~33%* |
| NGO | 4 | 1 | ~25% |

*nach dem Entfernen von `council` als Signal niedriger; auf der größeren n=1000-Stichprobe
gemessen: Forschung ~28%, Behörde ~30%, NGO ~52% (siehe unten, andere Rundungsbasis).

**Precision: keine beobachteten Fehlalarme** in den Stichprobenchecks nach Iteration 3 — die
Regel ist konservativ (meldet lieber nichts, als falsch zu liegen), aber genau deshalb recall-limitiert.

## Ergebnis auf der vollen Datenbank (735.242 Orgs)

Siehe `results/rule_classification_summary.txt` für den kompletten Lauf. Kernzahlen:

| Kategorie | Von der Regel gefunden | Wahrer Anteil (aus 1000er-Stichprobe) | Recall |
|---|---:|---:|---:|
| Forschung | 4,33% | 16,9% | ~26% |
| NGO | 0,83% | 1,7% | ~49% |
| Behörde | 0,33% | 1,0% | ~33% |
| Unternehmen | 0% (nicht adressiert) | 22,3% | 0% |
| Community | 0% (nicht adressiert) | 20,3% | 0% |

**Fazit:** Die Regel ist brauchbar als sehr konservativer Vorfilter (z. B. um sicher zu sein, dass
ein Treffer wirklich Forschung ist), aber für eine vollständige Klassifikation ungeeignet — sie
erreicht nur eine Minderheit der echten Fälle und deckt zwei Drittel der Population (Unternehmen +
Community) überhaupt nicht ab. Empfehlung und Alternativen: [next_steps.md](next_steps.md).

## Skripte ausführen

```bash
# Regel gegen den 100er-Goldstandard testen (druckt Precision/Recall)
python scripts/evaluate_rules_on_goldstandard.py

# Regel auf die volle organizations-Tabelle anwenden (nur lesend, ~3 Minuten Laufzeit)
python scripts/classify_organizations_by_rules.py
```

Beide Skripte laden den ROR-Datendump von einem fest hinterlegten lokalen Pfad
(`ror/v2.10-2026-07-20-ror-data.csv`) — dieser ist **nicht** Teil des Repos (35 MB). Vor dem
Ausführen erneut von [Zenodo](https://doi.org/10.5281/zenodo.6347574) laden und den Pfad in beiden
Skripten anpassen (`ROR_CSV`-Konstante).
