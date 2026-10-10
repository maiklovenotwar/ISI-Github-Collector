---
title: Kategorien — Definitionen, Beispiele, Einschränkungen (Management-Fassung)
purpose: Verständliche, mit Belegen aus echten Profildaten untermauerte Fassung der Taxonomie für nicht-technische Stakeholder
status: final für n=1000; siehe taxonomy.md für die technische Langfassung
---

# Kategorien — Definitionen, Beispiele, Einschränkungen

Diese Fassung ist die für den Fachvorgesetzten aufbereitete Version von
[taxonomy.md](taxonomy.md). Sie verzichtet auf technischen Jargon (ROR, Regex,
Signalquellen-Tabellen) und belegt jedes Beispiel mit dem tatsächlichen
Datenbank-Feld, aus dem sich die Einordnung ergibt.

## Ausgangslage

Der Auftrag umfasste ursprünglich drei Kategorien (Forschung, Unternehmen,
Einzelperson). Bei der Durchsicht von 1.000 zufällig gezogenen Organisationen
(Methodik: [goldstandard_methodology.md](goldstandard_methodology.md)) zeigte
sich, dass diese drei zusammen nur 43% der Fälle abdecken. Drei weitere
Kategorien wurden ergänzt, um den Rest sinnvoll abzubilden, statt ihn in
unpassende Kategorien zu zwingen. Anteile pro Kategorie:
[population_estimate.md](population_estimate.md).

## Die Kategorien

### 1. Forschung
**Definition:** Verbindung zu einer Hochschule oder Forschungseinrichtung
erkennbar.
**Beispiel:** `ThundeRatz` — Name: "ThundeRatz Robotics Team", Standort:
"Prédio de Engenharia Mecatrônica, Poli-USP, São Paulo" (Gebäude der
Universität São Paulo). Name und Standort belegen es direkt.

### 2. Unternehmen
**Definition:** Kommerzieller Hintergrund erkennbar.
**Beispiel:** `itbh-at` — Name: "IT Beratung Hermann **GmbH**", Beschreibung:
"Consulting & Development", Kontakt über eigene Firmendomain
(office@itbh.at). Rechtsform, Geschäftszweck und Firmenkontakt stehen direkt
im Profil.

### 3. Behörde
**Definition:** Staatliche/öffentliche Trägerschaft erkennbar.
**Beispiel:** `livingskyschooldivision` — Beschreibung: "A K-12 public school
division in Saskatchewan, Canada". Die Bio benennt die öffentliche
Trägerschaft wörtlich.

### 4. NGO / Gemeinnützige Organisation
**Definition:** Gemeinnützige Rechtsform ausdrücklich erkennbar (Foundation,
Stiftung, e.V. o. ä.).
**Beispiel:** `open-avenues-foundation` — "Foundation" direkt im Namen.

### 5. Community / informelles Projekt
**Definition:** Mehrere Anzeichen für ein offenes, nicht-kommerzielles
Projekt zusammen (kostenlose Hosting-Adresse statt Firmendomain, Hobby-Ton,
keine Rechtsform).
**Beispiel:** `nautilus-os` — Beschreibung: "An operating system made for
your browser... featuring games, themes, extensive customization", Web-
Adresse auf `vercel.app` (kostenloser Hosting-Dienst, keine Firmendomain).

### 6. Einzelner Contributor
**Definition:** Account gehört erkennbar einer einzelnen Person statt einer
Institution — meist erkennbar an einer Ich-Form in der Beschreibung oder
eindeutig privatem/informellem Ton, nicht an einem einzelnen Merkmal wie der
E-Mail-Adresse (siehe Einschränkung unten).
**Beispiel:** `makgirlygirl` — Beschreibung: "막걸리걸 입니다" (koreanisch,
wörtlich: "Ich bin Makgeolli-Girl"). Eine Institution beschreibt ihren
Zweck, keine Person stellt sich in der Ich-Form vor.

### Zusatz: Nicht klassifizierbar
**Definition:** Keine eigene inhaltliche Kategorie, sondern Kennzeichnung
für fehlende Information.
**Beispiel:** `gostub` — Name, Beschreibung, Web-Adresse, Standort, E-Mail:
alles leer.
Betrifft ca. 34% der Gesamt-Population — das heißt aber nicht, dass ein
Drittel der Profile leer ist: nur 12,6% sind tatsächlich komplett leer (kein
Name, keine Bio, kein Blog, keine Location, keine Firma — per direkter
DB-Abfrage bestätigt). Der Rest hat irgendein Feld befüllt, aber nicht genug
für eine Einordnung.

## Einschränkungen der Methode

**a) "Einzelner Contributor" ist die unschärfste Kategorie.**
Offene Frage: Warum sollte sich eine Einzelperson überhaupt als
"Organisation" (statt als normaler Nutzer-Account) bei GitHub anmelden?
Plausible, aber nicht belegbare Gründe: eigene Marke/Kanal-Identität getrennt
vom privaten Profil (z. B. YouTuber), historisch unterschiedliche kostenlose
Kontingente zwischen Nutzer- und Organisationskonten, oder die Absicht,
später Mitstreiter einzuladen. Eine private Gmail-Adresse als Kontakt reicht
**nicht** als Beleg, da auch echte kleine Vereine, informelle Gruppen und
kleine Firmen ohne eigene Domain Gmail nutzen — dieses Merkmal wurde deshalb
nicht mehr als alleiniges Kriterium verwendet. Auch die Ich-Form bleibt eine
Interpretation, kein hartes Kriterium. Kleinste Kategorie (3,7% der
Stichprobe) — begrenzter Effekt auf die Gesamtzahlen, aber größte
Einzelfall-Unsicherheit.

**b) Ein Teil der Urteile stützt sich auf Wiedererkennung, nicht auf
Profildaten.**
Bei manchen der 1.000 Fälle (z. B. `belastingdienst`, die niederländische
Steuerbehörde) enthielt das Profil selbst keine Hinweise auf staatliche
Trägerschaft — die Einordnung beruhte darauf, den Namen als bekannte
Institution zu erkennen, nicht auf einem sichtbaren Feld. Analog bei
`xenia-canary` (Community): alle Felder leer, Einordnung nur über Wieder-
erkennung des bekannten Open-Source-Projekts. Das ist kein Einzelfall,
sondern eine strukturelle Eigenschaft der Methode.

**c) Selbst-Konsistenz: 80%.**
Ein interner Blind-Recheck (dieselben Fälle nach einiger Zeit erneut, ohne
Kenntnis der ursprünglichen Einordnung, beurteilt) ergab eine
Selbst-Konsistenz von 80% — d. h. in etwa jedem fünften Fall kam bei
erneuter Prüfung ein anderes Urteil heraus. Das ist die Größenordnung der
Unsicherheit, die hinter jeder Prozentzahl in
[population_estimate.md](population_estimate.md) steht. Für eine
belastbarere Zahl (z. B. für eine externe Präsentation) würde sich ein
zweiter, unabhängiger Rater oder eine größere Stichprobe empfehlen.

## Verwandte Dokumente

- [taxonomy.md](taxonomy.md) — technische Langfassung mit Signalquellen-Tabelle
- [population_estimate.md](population_estimate.md) — Anteile pro Kategorie, hochgerechnet
- [goldstandard_methodology.md](goldstandard_methodology.md) — wie die 1000er-Stichprobe gezogen und bewertet wurde
- [next_steps.md](next_steps.md) — Empfehlung zur Skalierung via LLM-Klassifikation
