-- Migration: Add ccTLD enrichment columns to organizations table
-- Date: 2026-02-24
-- Description: Adds columns for country code derived from blog/email ccTLD

-- Check current schema before running:
-- DESCRIBE organizations;

-- Add new columns
ALTER TABLE organizations
    ADD COLUMN country_code_cctld VARCHAR(2) DEFAULT NULL COMMENT 'ISO country code from blog/email ccTLD',
    ADD COLUMN cctld_source VARCHAR(20) DEFAULT NULL COMMENT 'Source: blog, email, or both',
    ADD COLUMN cctld_enriched_at DATETIME DEFAULT NULL COMMENT 'Timestamp of ccTLD enrichment',
    ADD COLUMN cctld_run_id VARCHAR(36) DEFAULT NULL COMMENT 'UUID of enrichment run (for revert)';

-- Add index for efficient revert
CREATE INDEX idx_organizations_cctld_run_id ON organizations(cctld_run_id);

-- Revert this migration:
-- ALTER TABLE organizations
--     DROP INDEX idx_organizations_cctld_run_id,
--     DROP COLUMN country_code_cctld,
--     DROP COLUMN cctld_source,
--     DROP COLUMN cctld_enriched_at,
--     DROP COLUMN cctld_run_id;
