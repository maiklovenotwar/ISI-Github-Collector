-- Migration: Add github_user_id/github_type to repository_contributors
-- Date: 2026-08-08
-- Description: Ergaenzt repository_contributors um die stabile numerische
--   GitHub-User-ID (ueberlebt Login-Umbenennungen, im Gegensatz zu github_login)
--   und den von GitHub gelieferten Account-Typ (User/Bot), aus dem
--   Contributors-Listen-Endpoint. Grundlage fuer robusteres Matching gegen
--   contributors.id (das dort bereits die GitHub-User-ID als PK nutzt, siehe
--   database.py:insert_contributor) sowie zum Anlegen fehlender
--   contributors-Stub-Zeilen fuer bislang unbekannte Contributors.
--
-- Check current schema before running:
-- DESCRIBE repository_contributors;

ALTER TABLE repository_contributors
    ADD COLUMN github_user_id INT DEFAULT NULL COMMENT 'Stabile numerische GitHub-User-ID (== contributors.id)' AFTER github_login,
    ADD COLUMN github_type VARCHAR(20) DEFAULT NULL COMMENT 'GitHub-Accounttyp laut API (User, Bot, ...)' AFTER github_user_id,
    ADD INDEX idx_repository_contributors_github_user_id (github_user_id);

-- Verify migration:
-- DESCRIBE repository_contributors;
-- SELECT COUNT(*) FROM repository_contributors WHERE github_user_id IS NOT NULL;
