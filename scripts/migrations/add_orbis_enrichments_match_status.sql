-- Migration: Add match_status to orbis_enrichments
-- Date: 2026-10-19
-- Description: orbis_enrichments stored the Match-Type of an Orbis match but not its
--   Match-Status (see CONTEXT.md). "unique" and "consensus" matches could not be told apart,
--   and a resolved "pending" case was recognisable only through match_type = 'pending'.
--   The new column records the Match-Status at match time: unique, consensus or pending.
--   Since the same change, a resolved pending case carries the Match-Type of the chosen
--   candidate (combined_high, name_only, ...) instead of 'pending'.
--
-- Rows written before this migration keep match_status NULL. On 2026-10-18 the table held
-- only six rows from two test runs, both rolled back.
--
-- Additive change (new nullable column); no data are modified. Save the output of
-- SHOW CREATE TABLE orbis_enrichments before running it.

ALTER TABLE orbis_enrichments
    ADD COLUMN match_status VARCHAR(20) NULL
    COMMENT 'Match-Status at match time: unique, consensus or pending (see CONTEXT.md)';

-- Verify migration:
-- SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM information_schema.columns
-- WHERE table_schema = DATABASE() AND table_name = 'orbis_enrichments'
--   AND column_name = 'match_status';

-- Revert this migration:
-- ALTER TABLE orbis_enrichments DROP COLUMN match_status;

-- SQLite: existing database files need the same statement in SQLite syntax:
-- ALTER TABLE orbis_enrichments ADD COLUMN match_status VARCHAR(20);
-- Newly created SQLite databases already contain the column.
