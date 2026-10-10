-- Migration: Add orbis_companies table (minimaler Firmen-Cache)
-- Date: 2026-08-09
-- Description: Bisher speicherte unsere DB beim Orbis-Matching NUR die
--   BvD-Kennnummer (organizations.bvd_id) plus Lauf-Metadaten
--   (orbis_enrichments) - keinen Firmennamen, keine Stadt, keine Website.
--   Wer nachschauen wollte, welche Firma sich hinter einer Kennnummer
--   verbirgt, musste erneut live bei Orbis (Oracle) nachfragen.
--
--   Diese Tabelle cached BEWUSST NUR die Felder, die beim Matching ohnehin
--   schon aus CONTACT_INFO abgefragt werden (Name, Land, Stadt, Website) -
--   für einen schnellen Überblick ohne Oracle-Verbindung. Tiefere Daten
--   (Finanzen, Rechtsform, Eigentümerstruktur, HQ-Signale) werden bewusst
--   NICHT hier dupliziert - dafür bleibt OrbisClient/Oracle die
--   Referenzquelle, sonst entsteht eine zweite, potenziell veraltende Kopie
--   der Orbis-Datenbank.
--
--   Ein Cache-Eintrag pro BVDID (nicht pro Match-Lauf) - mehrere
--   Organizations/Läufe können auf dieselbe Firma verweisen, ohne die
--   Firmendaten mehrfach zu speichern. Wird bei jedem echten
--   Enrichment-Schreibvorgang per Upsert aktualisiert (siehe
--   database.py: upsert_orbis_company()). NICHT Teil des Rollbacks -
--   der Cache ist BVDID-, nicht Lauf-spezifisch und bleibt bei einem
--   Rollback bewusst bestehen.

CREATE TABLE IF NOT EXISTS orbis_companies (
    bvd_id VARCHAR(50) PRIMARY KEY COMMENT 'ORBIS BvD-Identifier',
    name VARCHAR(255) COMMENT 'NAME_NATIVE aus Orbis CONTACT_INFO',
    country_iso VARCHAR(2) COMMENT 'CTRYISO aus Orbis CONTACT_INFO',
    city VARCHAR(255) COMMENT 'CITY_NATIVE aus Orbis CONTACT_INFO',
    website VARCHAR(255) COMMENT 'WEBSITE aus Orbis CONTACT_INFO',
    cached_at DATETIME NOT NULL COMMENT 'Zeitstempel dieses Snapshots',

    INDEX idx_orbis_companies_country (country_iso)
) COMMENT 'Minimaler Cache der Orbis-Firmendaten fuer schnellen Ueberblick ohne Oracle-Verbindung. Tiefere Daten weiterhin live bei Orbis abfragen.';

-- Verify migration:
-- DESCRIBE orbis_companies;
-- SELECT COUNT(*) FROM orbis_companies;
