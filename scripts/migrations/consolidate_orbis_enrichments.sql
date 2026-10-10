-- Migration: Firmendaten in orbis_enrichments konsolidieren, orbis_companies entfernen
-- Date: 2026-08-09
-- Description: orbis_companies (Firmen-Cache) und orbis_enrichments (Match-
--   Logbuch) haben sich überlappt, ohne dass eine der beiden Tabellen
--   allein die naheliegendste Frage beantworten konnte: "welche
--   GitHub-Organisation wurde mit welcher Firma (Name/Ort) verknüpft, und
--   ist dieser Match noch aktiv?" - dafür waren zwei Tabellen UND separate
--   Abfragen nötig.
--
--   orbis_companies kann nicht einfach zur "einen Wahrheit" werden, weil
--   sie pro BVDID nur EINE Zeile hat - das widerspricht dem Logbuch-Zweck
--   (mehrere Match-Versuche über Zeit, inkl. zurückgerollter). Deshalb:
--   Firmendaten NACH orbis_enrichments verschieben (dort sowieso schon pro
--   Match-Versuch vorhanden), orbis_companies komplett entfernen.
--
--   Ergebnis: EINE Tabelle beantwortet "wer wurde mit welcher Firma
--   verknüpft, wie hieß sie, wann, noch aktiv?" ohne Join.

ALTER TABLE orbis_enrichments
    ADD COLUMN company_name VARCHAR(255) DEFAULT NULL COMMENT 'NAME_NATIVE aus Orbis zum Zeitpunkt des Matches' AFTER bvd_id,
    ADD COLUMN company_country VARCHAR(2) DEFAULT NULL COMMENT 'CTRYISO aus Orbis' AFTER company_name,
    ADD COLUMN company_city VARCHAR(255) DEFAULT NULL COMMENT 'CITY_NATIVE aus Orbis' AFTER company_country,
    ADD COLUMN company_website VARCHAR(255) DEFAULT NULL COMMENT 'WEBSITE aus Orbis' AFTER company_city;

-- Bereits vorhandene Firmendaten aus orbis_companies übernehmen, bevor die
-- Tabelle entfernt wird (betrifft nur Zeilen, die im Rahmen des heutigen
-- Testens geschrieben wurden).
UPDATE orbis_enrichments e
JOIN orbis_companies c ON c.bvd_id = e.bvd_id
SET e.company_name = c.name,
    e.company_country = c.country_iso,
    e.company_city = c.city,
    e.company_website = c.website
WHERE e.company_name IS NULL;

DROP TABLE orbis_companies;

-- Verify migration:
-- DESCRIBE orbis_enrichments;
-- SHOW TABLES LIKE 'orbis_companies';  -- sollte leer sein
