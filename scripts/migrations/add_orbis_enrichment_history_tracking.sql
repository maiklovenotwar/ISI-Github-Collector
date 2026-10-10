-- Migration: orbis_enrichments als dauerhaftes Logbuch + Match-ID auf organizations
-- Date: 2026-08-09
-- Description: Vorher hat rollback_orbis_enrichment_run() die Zeilen in
--   orbis_enrichments für den betroffenen Lauf komplett GELÖSCHT - nach
--   einem Rollback gab es dadurch nirgendwo mehr eine Spur, welche
--   GitHub-Organisation ursprünglich mit welcher Orbis-Firma verknüpft war
--   (weder auf `organizations` selbst, noch im "Logbuch"). Das widerspricht
--   dem Zweck eines Logbuchs (Nachprüfbarkeit) und wurde beim Testen des
--   Rollbacks entdeckt.
--
--   Fix:
--   1. orbis_enrichments bekommt `reverted_at` - ein Rollback setzt das Feld
--      statt die Zeile zu löschen. Damit bleibt JEDER Match-Versuch für
--      immer nachprüfbar (Logbuch-Prinzip).
--   2. organizations bekommt `current_orbis_enrichment_id` - zeigt direkt
--      auf die aktuell aktive Logbuch-Zeile (die "Match-ID"). Wird beim
--      Rollback auf NULL gesetzt (kein aktiver Match mehr), aber die
--      referenzierte Logbuch-Zeile selbst bleibt bestehen.

ALTER TABLE orbis_enrichments
    ADD COLUMN reverted_at DATETIME DEFAULT NULL COMMENT 'Zeitstempel eines Rollbacks - NULL = weiterhin aktiv' AFTER enriched_at,
    ADD INDEX idx_orbis_enrichments_reverted_at (reverted_at);

ALTER TABLE organizations
    ADD COLUMN current_orbis_enrichment_id INT DEFAULT NULL COMMENT 'FK zu orbis_enrichments.id - der aktuell aktive Match, NULL nach Rollback' AFTER orbis_run_id,
    ADD CONSTRAINT fk_organizations_current_enrichment
        FOREIGN KEY (current_orbis_enrichment_id) REFERENCES orbis_enrichments(id)
        ON DELETE SET NULL;

-- Verify migration:
-- DESCRIBE orbis_enrichments;
-- DESCRIBE organizations;
