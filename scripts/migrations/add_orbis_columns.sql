-- Migration: Add Orbis enrichment columns to organizations table
-- Date: 2026-02-19
-- Description: Adds columns for Orbis data enrichment (BvD-ID, country code, match type, etc.)

-- Check current schema before running:
-- DESCRIBE organizations;

-- Add new columns
ALTER TABLE organizations
    ADD COLUMN bvd_id VARCHAR(50) DEFAULT NULL COMMENT 'Orbis BvD-Identifier',
    ADD COLUMN country_code_orbis VARCHAR(2) DEFAULT NULL COMMENT 'ISO country code from Orbis (ground truth)',
    ADD COLUMN orbis_match_type VARCHAR(20) DEFAULT NULL COMMENT 'Match method: url, name, fuzzy',
    ADD COLUMN orbis_enriched_at DATETIME DEFAULT NULL COMMENT 'Timestamp of Orbis enrichment',
    ADD COLUMN orbis_run_id VARCHAR(36) DEFAULT NULL COMMENT 'UUID of enrichment run (for revert)';

-- Add indexes for efficient queries
CREATE INDEX idx_organizations_bvd_id ON organizations(bvd_id);
CREATE INDEX idx_organizations_orbis_run_id ON organizations(orbis_run_id);

-- Verify migration:
-- DESCRIBE organizations;
-- SELECT COUNT(*) FROM organizations WHERE bvd_id IS NOT NULL;
