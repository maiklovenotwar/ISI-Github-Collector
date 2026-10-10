---
title: Methodik der Goldstandard-Erstellung
purpose: Nachvollziehbar machen, wie die 1000 Handklassifikationen entstanden sind und mit welcher Sorgfalt
status: abgeschlossen für n=1000, jederzeit erweiterbar
---

# Methodik

## Ziehung

Alle Stichproben wurden per `ORDER BY RAND() LIMIT n` direkt gegen die `organizations`-Tabelle
gezogen (735.242 Zeilen). Abgefragte Felder: `login, name, company, bio, blog, location, email,
followers, public_repos, public_members, created_at`.

**Zwei unterschiedliche Stichproben-Typen wurden gezogen, mit unterschiedlichem Zweck:**

| Datei | n | Ziehungsart | Zweck |
|---|---:|---|---|
| `raw_batch1_pilot_100.csv` | 100 | **Stratifiziert**: 20 High-Follower (>500), 30 Mid-Tier mit gesetztem Blog-Feld, 50 rein zufällig | Regel-Prototyp testen (braucht Fälle mit viel Signal) |
| `raw_batch2/3/4_random_*.csv` | 500+200+250 | **Rein zufällig**, keine Filter | Populations-Anteile schätzen (muss repräsentativ sein) |

Nur die rein zufälligen 950 Zeilen (50 aus Batch 1 + 950 aus Batch 2-4 — korrekt: 50+500+200+250=1000)
fließen in die Populations-Schätzung ein. Die stratifizierten Segmente A/B (High-Follower/Mid-Tier)
aus Batch 1 sind bewusst **nicht** Teil der 1000er-Populations-Stichprobe, weil sie überproportional
viele "hat Signal"-Fälle enthalten und die Prozentsätze verzerren würden.

## Klassifikationsprozess

Jede Organisation wurde einzeln anhand der abgefragten Felder beurteilt — Name, Bio, Blog-Domain,
Location, E-Mail, Follower-/Repo-Zahlen. Für die ersten 100 (Batch 1) wurde zusätzlich mit drei
Kontrollmechanismen gegengeprüft (siehe unten); für die Erweiterung auf n=1000 (Batch 2-4) wurde
aus Zeit-/Aufwandsgründen direkt geurteilt, ohne Einzelfall-Websuche, dafür in mehreren
unabhängigen Chargen (50, 200, 250) mit jeweils eigener Vollständigkeitsprüfung.

### Kontrollmechanismen (angewendet auf Batch 1, n=100)

1. **Regel-Abgleich**: Eine einfache Keyword-/Domain-Regel wurde parallel gerechnet und mit dem
   manuellen Urteil verglichen — Abweichungen wurden markiert und einzeln erklärt.
2. **Externe Verifikation**: Bei ~10-15% der unsicheren Fälle wurde die tatsächliche Website
   aufgerufen (WebFetch), um das Urteil zu bestätigen oder zu korrigieren (z. B. `you-apps` wurde
   dadurch von "vermutlich Unternehmen" zu "tatsächlich Community-Projekt" korrigiert).
3. **Blind-Recheck**: 10 zufällige Fälle wurden ohne Blick auf das vorherige Label erneut beurteilt.
   Ergebnis: 80% Selbstkonsistenz. Die zwei Abweichungen entstanden dort, wo das Erstlabel auf
   nicht in der Datenbank stehendem Hintergrundwissen beruhte (z. B. Wiedererkennen eines
   spezifischen Forschungssoftware-Projekts) — ein Hinweis darauf, dass manche Labels weniger
   aus den reinen DB-Feldern als aus Weltwissen abgeleitet wurden, was für Reproduzierbarkeit
   relevant ist.

### Bekannte Fehlerquelle in Batch 2 (korrigiert)

Bei der ersten Verschriftlichung von Batch 2 (500 Zeilen) wurden versehentlich 10 aufeinanderfolgende
Zeilen (Positionsverschiebung beim Durcharbeiten) sowie 3 weitere Einzelfälle beim Übertragen in die
CSV-Datei ausgelassen (487 statt 500 Zeilen). Der Fehler wurde durch einen automatisierten
Abgleich zwischen Rohdaten und CSV aufgedeckt (`login`-Mengenvergleich) und durch Nachklassifikation
der 13 fehlenden Zeilen behoben — seither wird jede Charge direkt nach dem Schreiben automatisiert
auf Vollständigkeit und Duplikate geprüft (siehe `scripts/build_goldstandard_samples.py`, das dies bei jedem
Lauf per `assert` erzwingt).

## Konvergenz der Populations-Schätzung

Die Prozentsätze wurden nach jeder Charge neu berechnet, um zu prüfen, ob sich die Schätzung
stabilisiert:

| n | Unternehmen | Community | Forschung | Unklar | Individual | NGO | Behörde |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 18% | 24% | 14% | 30% | 8% | 2% | 4% |
| 550 | 21,8% | 20,0% | 15,5% | 36,0% | 4,0% | 1,6% | 1,1% |
| 750 | 21,9% | 19,3% | 15,7% | 36,8% | 3,7% | 1,6% | 0,9% |
| **1000** | **22,3%** | **20,3%** | **16,9%** | **34,1%** | **3,7%** | **1,7%** | **1,0%** |

Zwischen n=550 und n=1000 bewegen sich alle Werte nur noch im Bereich der erwartbaren
Stichprobenschwankung — die Schätzung gilt als stabil genug für eine erste belastbare Aussage
gegenüber dem Fachvorgesetzten. Details und Interpretation: [population_estimate.md](population_estimate.md).

## Nachvollziehbarkeit

Jede der 1000 Zeilen in `data/goldstandard/population_sample_1000.csv` trägt den GitHub-Login als
eindeutigen Schlüssel — jede Einordnung lässt sich bei Bedarf am echten GitHub-Profil nachprüfen.
Die `batch`-Spalte zeigt, aus welcher Ziehungsrunde die Zeile stammt.
