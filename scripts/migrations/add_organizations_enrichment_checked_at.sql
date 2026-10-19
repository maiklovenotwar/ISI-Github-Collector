-- Migration: Add enrichment_checked_at to organizations
-- Date: 2026-10-09
-- Description: scripts/enrichment/enrich_org_data_graphql.py fetches profile data (name, blog,
--   email, location) from GitHub. Until now it recorded each fetch in
--   organizations.orbis_enriched_at and organizations.orbis_run_id, although these
--   columns describe Orbis matches. A non-NULL orbis_enriched_at therefore did not
--   indicate an Orbis match, and a rollback of the Orbis pipeline erased the record of
--   the GitHub fetch. This migration gives the profile fetch its own column, with the
--   same meaning as contributors.enrichment_checked_at, and moves the existing
--   timestamps there.
--
-- Order: run this migration BEFORE using the updated scripts/enrichment/enrich_org_data_graphql.py;
--   the script fails with "Unknown column 'enrichment_checked_at'" otherwise.
--
-- Check before running (read-only). The Orbis pipeline always sets bvd_id together with
-- orbis_enriched_at, so a timestamp on a row without bvd_id stems from the GitHub fetch.
-- The second statement below changes exactly the rows counted in the line
-- "has_timestamp = 1, has_bvd_id = 0":
--
-- SELECT (orbis_run_id IS NOT NULL)                AS has_run_id,
--        (orbis_enriched_at IS NOT NULL)           AS has_timestamp,
--        (bvd_id IS NOT NULL)                      AS has_bvd_id,
--        (current_orbis_enrichment_id IS NOT NULL) AS has_current_match,
--        COUNT(*)                                  AS organizations
-- FROM organizations
-- GROUP BY 1, 2, 3, 4;

-- 1. New column.
ALTER TABLE organizations
    ADD COLUMN enrichment_checked_at DATETIME DEFAULT NULL
    COMMENT 'Timestamp of the last profile fetch attempt (also set when GitHub returned nothing)';

-- 2. Carry over the timestamps written by the GitHub profile fetch. A row that the
--    updated profile script has already stamped keeps its newer enrichment_checked_at.
--    On a large table, run steps 2 and 3 in blocks over id (e.g. 10,000 ids), each block
--    in one transaction with step 2 before step 3, and save the affected rows first
--    (id, orbis_enriched_at, orbis_run_id, enrichment_checked_at, bvd_id).
UPDATE organizations
SET enrichment_checked_at = orbis_enriched_at
WHERE orbis_enriched_at IS NOT NULL
  AND bvd_id IS NULL
  AND enrichment_checked_at IS NULL;

-- 3. Remove them from the Orbis column, so that orbis_enriched_at IS NOT NULL again
--    means "this organization has an Orbis match".
UPDATE organizations
SET orbis_enriched_at = NULL
WHERE orbis_enriched_at IS NOT NULL
  AND bvd_id IS NULL;

-- Not changed: organizations with an Orbis match (bvd_id IS NOT NULL). If such a row was
-- fetched from GitHub before it was matched, the Orbis pipeline has already overwritten
-- the fetch timestamp; it cannot be recovered and enrichment_checked_at stays NULL. These
-- rows are not fetched again, because the script only selects organizations whose profile
-- fields are all empty.
--
-- Not changed: orbis_run_id. The column is no longer written by any script; the values
-- remain as a record of which earlier GitHub fetch run touched a row.

-- Verify migration:
-- DESCRIBE organizations;
-- SELECT COUNT(*) FROM organizations WHERE orbis_enriched_at IS NOT NULL AND bvd_id IS NULL;
--   (expected: 0)
-- SELECT COUNT(*) FROM organizations WHERE enrichment_checked_at IS NOT NULL;
--   (expected: the rows that already carried enrichment_checked_at before, plus the rows
--   counted under "has_timestamp = 1, has_bvd_id = 0", minus the rows in both groups;
--   on 2026-10-19 the overlap was empty and the result 410,066)

-- Restore steps 2 and 3 from the CSV backup, one execution per saved row:
-- UPDATE organizations SET orbis_enriched_at = :orbis_enriched_at, enrichment_checked_at = :enrichment_checked_at WHERE id = :id;

-- Revert this migration:
-- UPDATE organizations
-- SET orbis_enriched_at = enrichment_checked_at
-- WHERE enrichment_checked_at IS NOT NULL AND bvd_id IS NULL;
-- ALTER TABLE organizations DROP COLUMN enrichment_checked_at;

-- SQLite: existing database files need only the first statement, in SQLite syntax:
-- ALTER TABLE organizations ADD COLUMN enrichment_checked_at DATETIME;
-- followed by the two UPDATE statements above, which are valid in SQLite unchanged.
-- Newly created SQLite databases already contain the column.
