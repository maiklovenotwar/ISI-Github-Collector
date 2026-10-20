-- Migration: Align the production schema with the models
-- Date: 2026-10-20
-- Description: A functional test rebuilt the database with create_all and compared it with
--   the production schema. The models now declare what only the production database had
--   (database-side defaults, the indexes idx_contributors_cctld and
--   idx_orbis_enrichments_reverted_at). This file covers the opposite direction:
--   - contributors.cctld_source was created with VARCHAR(10) by
--     add_cctld_contributors_no_index.py; the model and organizations use VARCHAR(20).
--   - Four indexes duplicate another index on the same column (two of them the primary key).
--
-- No data are modified: the column is widened, and the dropped indexes have an equivalent
-- that remains. Both changes run in place without copying the table. Save the output of
-- SHOW CREATE TABLE for contributors, organizations and repositories before running it.
--
-- Not covered: repositories.country_code_inf exists only in the production database and is
-- not used by the code. Dropping it would remove data, so it stays.

ALTER TABLE contributors
    MODIFY COLUMN cctld_source VARCHAR(20) DEFAULT NULL,
    ALGORITHM = INPLACE, LOCK = NONE;

-- duplicate of PRIMARY KEY (id)
ALTER TABLE contributors DROP INDEX idx_contributors_id, ALGORITHM = INPLACE, LOCK = NONE;
-- duplicate of PRIMARY KEY (id)
ALTER TABLE organizations DROP INDEX idx_orgs_id, ALGORITHM = INPLACE, LOCK = NONE;
-- duplicate of idx_organizations_country_code (country_code)
ALTER TABLE organizations DROP INDEX idx_orgs_country, ALGORITHM = INPLACE, LOCK = NONE;
-- duplicate of idx_repositories_organization_id (organization_id)
ALTER TABLE repositories DROP INDEX idx_repos_orgid, ALGORITHM = INPLACE, LOCK = NONE;

-- Verify migration:
-- SHOW INDEX FROM contributors; SHOW INDEX FROM organizations; SHOW INDEX FROM repositories;
-- SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.columns
-- WHERE table_schema = DATABASE() AND table_name = 'contributors'
--   AND column_name = 'cctld_source';

-- Revert this migration (only the indexes; the wider column needs no revert):
-- CREATE INDEX idx_contributors_id ON contributors (id);
-- CREATE INDEX idx_orgs_id ON organizations (id);
-- CREATE INDEX idx_orgs_country ON organizations (country_code);
-- CREATE INDEX idx_repos_orgid ON repositories (organization_id);
