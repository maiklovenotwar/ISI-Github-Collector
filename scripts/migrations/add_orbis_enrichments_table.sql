-- Migration: Add orbis_enrichments table (Hybrid-Ansatz)
-- Date: 2026-08-07
-- Description: Implementiert den "Hybrid-Ansatz" aus dem wiederhergestellten
--   Strategiedokument (Orbis-Notizen (intern, ältere Entwürfe):
--   enrichment/orbis_matching_strategy_v2.md, Abschnitt 8), der nie
--   umgesetzt wurde.
--
-- WICHTIG: bvd_id, country_code_orbis und orbis_enriched_at existieren
--   bereits auf `organizations` (siehe add_orbis_columns.sql, 2026-02-19) -
--   diese Migration fügt dort KEINE Spalten hinzu, nur die neue Tabelle für
--   Lauf-Historie/Rollback-Fähigkeit. orbis_match_type und orbis_run_id auf
--   `organizations` bleiben unangetastet (orbis_run_id wird von
--   scripts/enrichment/enrich_org_data_graphql.py für einen unabhängigen Zweck
--   wiederverwendet - NICHT anfassen).
--
-- Check current schema before running:
-- DESCRIBE organizations;
-- SHOW TABLES LIKE 'orbis_enrichments';
--
-- KORREKTUR (beim ersten echten Anwenden dieser Migration entdeckt):
-- organization_id war ursprünglich als BIGINT deklariert, aber
-- organizations.id ist tatsächlich INT - MySQL lehnt einen FK mit
-- inkompatiblen Spaltentypen ab. Auf INT korrigiert (passt auch zum
-- SQLAlchemy-Modell in models.py, das von Anfang an korrekt Integer nutzte -
-- der Fehler war nur in dieser SQL-Datei).

CREATE TABLE IF NOT EXISTS orbis_enrichments (
    id INT PRIMARY KEY AUTO_INCREMENT,
    organization_id INT NOT NULL COMMENT 'FK zu organizations.id',
    bvd_id VARCHAR(50) NOT NULL COMMENT 'ORBIS BvD-Identifier',
    match_type VARCHAR(20) NOT NULL COMMENT 'combined_high, combined_medium, combined_low, name_only, ...',
    hq_method VARCHAR(30) DEFAULT NULL COMMENT 'HQ-Kaskaden-Signal, z.B. ROOT_DOMAIN_PRESENCE, BVDID_FALLBACK',
    score INT NOT NULL COMMENT 'Matching-Score',
    blog_matched BOOLEAN NOT NULL DEFAULT FALSE COMMENT 'Blog-Domain gematcht',
    run_id VARCHAR(36) NOT NULL COMMENT 'UUID des Enrichment-Laufs',
    enriched_at DATETIME NOT NULL COMMENT 'Zeitstempel',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    INDEX idx_orbis_enrichments_run_id (run_id),
    INDEX idx_orbis_enrichments_bvd_id (bvd_id),
    INDEX idx_orbis_enrichments_score (score),
    INDEX idx_orbis_enrichments_match_type (match_type),
    UNIQUE KEY uk_org_run (organization_id, run_id)
) COMMENT 'ORBIS Enrichment Metadaten (Lauf-Historie, Rollback-fähig)';

-- Rollback eines kompletten Laufs (siehe orbis_matching_strategy_v2.md, Abschnitt 8.7):
-- DELETE FROM orbis_enrichments WHERE run_id = '<run_id>';
-- UPDATE organizations o
--   LEFT JOIN orbis_enrichments e ON e.organization_id = o.id
--   SET o.bvd_id = e.bvd_id, o.country_code_orbis = NULL, o.orbis_enriched_at = NULL
--   WHERE o.orbis_run_id = '<run_id>' AND e.id IS NULL;

-- Verify migration:
-- DESCRIBE orbis_enrichments;
-- SELECT COUNT(*) FROM orbis_enrichments;
