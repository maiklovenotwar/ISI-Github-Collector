---
title: Empfehlung für das weitere Vorgehen
purpose: Optionen für den nächsten Schritt bewerten und eine klare Empfehlung geben
status: offen — Entscheidung liegt beim Fachvorgesetzten (Kostenfrage)
---

# Nächste Schritte

## Ausgangslage

Der regelbasierte Klassifikator (siehe [rule_based_classifier.md](rule_based_classifier.md)) hat
sich als deutlich recall-limitiert erwiesen (26-53% je Kategorie, deckt Unternehmen/Community gar
nicht ab). Vier mögliche Verbesserungsrichtungen wurden geprüft:

| Ansatz | Gemessener Effekt | Bewertung |
|---|---|---|
| Akronym-Abgleich mit Geo-Gegenprobe | Nicht umgesetzt; nur 31,6% der Orgs haben `country_code` gesetzt | Begrenzte Reichweite |
| Mehrsprachige Keywords | +0,13 Prozentpunkte auf 735K Orgs | Geringer Ertrag, empirisch geprüft |
| Größere Validierungsstichprobe | Verbessert nur die Messgenauigkeit, nicht den Recall selbst | Sinnvoll als Kontrolle, kein Hebel |
| **LLM-gestützte Klassifikation** | Bei manueller Prüfung (die methodisch das tut, was ein LLM tun würde) deutlich höherer Recall | **Größter Hebel** |

## Empfehlung: LLM-Batch-Klassifikation

### Warum das funktionieren sollte
Die 1000 Handklassifikationen in `data/goldstandard/population_sample_1000.csv` sind im Kern
genau das, was ein LLM-Klassifikator tun würde: Felder lesen, mit Weltwissen/Mustererkennung ein
Urteil bilden. Der Unterschied ist nicht die Qualität des Urteils, sondern der Mechanismus (siehe
unten) — die 1000 Zeilen sind faktisch bereits ein Nachweis, dass der Ansatz funktioniert, und
dienen gleichzeitig als Validierungsdatensatz für einen API-Lauf.

### Kostenschätzung
Für die komplette adressierbare Menge (~630K Orgs mit mindestens einem befüllten Feld, siehe
[population_estimate.md](population_estimate.md)) über die Anthropic Batch-API (50% Rabatt
gegenüber Standard-Preisen, kein Zeitdruck da asynchron):

| Modell | Geschätzte Kosten (Vollmenge) |
|---|---:|
| Claude Haiku 4.5 | ~100-110 USD |
| Claude Sonnet 5 | ~200 USD |

Für eine kleinere Teilmenge (z. B. 10.000 Orgs zur weiteren Validierung) skaliert das linear auf
~2-3 USD.

### Wichtige Klarstellung: Subscription vs. API
Diese Unterhaltung lief komplett über die bestehende Claude-Code-Subscription (kein zusätzlicher
Kostenpunkt für die 1000 Handklassifikationen). Ein Batch-Lauf über die Anthropic API ist davon
**komplett getrennt** — anderes Abrechnungsmodell (pay-per-token statt Nutzungskontingent), anderer
Zugriffsmechanismus (bis zu 100.000 parallele Anfragen statt seriellem Chat-Dialog). Es ist
**dasselbe zugrundeliegende Modell**, nur ein anderes Werkzeug für Masse statt Dialog. Deshalb
lässt sich "10.000 Orgs manuell im Chat weiterklassifizieren" technisch fortsetzen, würde aber
schätzungsweise 20+ weitere Chargen und mehrere Stunden brauchen — bei identischer Qualität zu
einem API-Batch-Lauf, der dasselbe in Minuten für ~2-3 USD erledigt.

### Realistische Erwartung an den Recall
Die Grenze liegt nicht bei der Methode, sondern bei den Daten: **34,1% der Orgs sind auch mit
vollem Weltwissen nicht klassifizierbar** (siehe [population_estimate.md](population_estimate.md)).
Ein LLM wird diese Grenze nicht wesentlich verschieben — realistisches Ziel ist eine zuverlässige
Klassifikation der ~66% mit ausreichendem Signal, nicht "alles".

## Offene Entscheidung

Der Fachvorgesetzte hat noch nicht entschieden, ob die Kosten getragen werden sollen (Stand
2026-07-24). Bis zur Klärung: kein API-Key im Projekt hinterlegt, keine Kosten angefallen.

**Wenn die Freigabe kommt**, sind die nächsten konkreten Schritte:
1. Anthropic-API-Key anlegen (console.anthropic.com), in `.env` hinterlegen (nicht committen!).
2. Klassifikations-Prompt entwerfen, basierend auf [taxonomy.md](taxonomy.md) — insbesondere die
   dort dokumentierten Grenzfälle (Bildung vs. Forschung, Konzern-Unterkonten, generische
   Wort-Kollisionen) explizit im Prompt adressieren.
3. Testlauf gegen `data/goldstandard/population_sample_1000.csv` (bereits vorhanden, spart eine
   erneute manuelle Validierungsrunde).
4. Bei zufriedenstellendem Ergebnis: Batch-Lauf auf die Vollmenge, Ergebnis als neue Spalte in der
   `organizations`-Tabelle persistieren (schreibender DB-Eingriff — vorher separat abstimmen).

## Verzahnung mit `orbis_matching`

Sobald die dortige Firmen-Matching-Pipeline eine verlässliche Unternehmen-Erkennung liefert
(aktueller Stand dort: Name-Similarity-Gate + Geo-Disambiguierung in Arbeit), sollte deren Ergebnis
direkt als "Unternehmen"-Signal in diese Klassifikation einfließen, statt es separat neu
aufzubauen — beide Workstreams lösen denselben Teil des Problems aus unterschiedlichen Richtungen.
