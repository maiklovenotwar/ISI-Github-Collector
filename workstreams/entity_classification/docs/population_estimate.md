---
title: Populations-Schätzung — Zusammensetzung der GitHub-Organizations-Tabelle
purpose: Belastbare Anteile pro Kategorie liefern, Basis für Aussagen an den Fachvorgesetzten
status: final für n=1000, Konfidenzintervalle nicht formal berechnet (siehe Einschränkungen)
---

# Populations-Schätzung

Grundlage: `data/goldstandard/population_sample_1000.csv`, 1000 rein zufällig gezogene und
manuell klassifizierte Organisationen (Methodik: [goldstandard_methodology.md](goldstandard_methodology.md)).
Grundgesamtheit: 735.242 Organisationen in der Collector-Datenbank (Stand 2026-07-24).

## Ergebnis

| Kategorie | Anteil (n=1000) | Hochgerechnet auf 735.242 |
|---|---:|---:|
| Unternehmen | 22,3% | ~163.900 |
| Community/informelles Projekt | 20,3% | ~149.250 |
| Forschung | 16,9% | ~124.260 |
| **Nicht klassifizierbar** | 34,1% | ~250.720 |
| Einzelner Contributor | 3,7% | ~27.200 |
| NGO/Nonprofit | 1,7% | ~12.500 |
| Behörde | 1,0% | ~7.350 |

## Wie die "Nicht klassifizierbar"-Zahl zu verstehen ist

34,1% heißt **nicht**, dass ein Drittel der Orgs leere Profile haben — nur 12,6% sind das
tatsächlich (kein Name, keine Bio, kein Blog, keine Location, keine Firma — per direkter
DB-Abfrage bestätigt). Der Rest der "Nicht klassifizierbar"-Fälle hat *irgendein* Feld befüllt,
aber nicht genug, um zwischen den Kategorien zu unterscheiden (z. B. nur ein Login-Name ohne
weiteren Kontext, oder ein mehrdeutiger Firmenname ohne Domain). Das ist eine reale Grenze der
Klassifizierbarkeit aus GitHub-Metadaten allein — auch ein sehr leistungsfähiges Sprachmodell wird
diese Grenze nicht wesentlich verschieben können, da schlicht keine Information vorhanden ist.

## Wichtige methodische Einschränkung

Die Prozentsätze beruhen auf **manueller Einzelfallbeurteilung mit Weltwissen** (Wiedererkennen
bekannter Firmen/Institutionen, Sprachverständnis für nicht-englische Bios, Interpretation von
Domainmustern) — nicht auf einer mechanisch reproduzierbaren Regel. Der Blind-Recheck (siehe
Methodik-Dokument) ergab 80% Selbst-Konsistenz, was die Größenordnung der Unsicherheit andeutet,
aber kein formales Konfidenzintervall ersetzt. Für eine belastbarere Zahl (z. B. für eine externe
Präsentation) würde sich ein zweiter, unabhängiger Rater oder eine größere Stichprobe empfehlen.

## Einordnung für den Fachvorgesetzten

- Der Auftrag lautete auf drei Kategorien (Forschung/Unternehmen/Individual) — diese drei machen
  zusammen nur **43%** der Population aus. Die übrigen 57% verteilen sich auf Community-Projekte
  (20%), nicht klassifizierbare Fälle (34%) und die kleineren Kategorien Behörde/NGO (3%).
- **Unternehmen ist die größte einzelne Kategorie** (22%), aber die Zahl "Anzahl Unternehmen"
  überzählt reale Firmen systematisch, weil große Konzerne oft mehrere GitHub-Org-Accounts
  betreiben (siehe [taxonomy.md](taxonomy.md) — Abschnitt "Konzern-Unterkonten").
- Die Kategorie **Forschung (17%)** ist größer als ursprünglich erwartet — plausibel, da GitHub in
  Wissenschaft/Lehre sehr verbreitet ist (Kursrepos, Labs, studentische Projekte).
