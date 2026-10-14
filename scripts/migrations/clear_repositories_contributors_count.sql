-- Migration: Clear repositories.contributors_count
-- Date: 2026-10-14
-- Description: enrich_repository_stats.py hat contributors_count mit 0 beschrieben, obwohl
--   die Zahl nicht erhoben wird (Befund K4). Am 2026-10-14 standen alle 27.465.002
--   Repositories mit Statistik auf 0, keines hoeher. Seit dem Fix bleibt die Spalte NULL;
--   diese Migration setzt die vorhandenen Nullen ebenfalls auf NULL.
--
-- Keine Sicherung noetig: Vorher zaehlen (erwartet 27.465.002 Zeilen mit 0, keine > 0).
-- Alle betroffenen Werte sind 0; Zuruecksetzen hiesse wieder 0 schreiben.
--
-- Vorher pruefen:
-- SELECT COUNT(*) FROM repositories WHERE contributors_count = 0;
-- SELECT COUNT(*) FROM repositories WHERE contributors_count > 0;   -- erwartet 0
--
-- Ausfuehrung in Bloecken ueber den Primaerschluessel, damit keine einzelne Transaktion
-- 27,5 Mio. Zeilen sperrt. :a und :b laufen in Schritten von 500.000 ueber
-- MIN(id) .. MAX(id); nach jedem Block COMMIT. Erst einen Block allein ausfuehren, die
-- Dauer messen und daraus die Gesamtdauer schaetzen.

UPDATE repositories
SET contributors_count = NULL
WHERE contributors_count = 0
  AND id BETWEEN :a AND :b;

-- Nachher pruefen (erwartet 0):
-- SELECT COUNT(*) FROM repositories WHERE contributors_count = 0;
