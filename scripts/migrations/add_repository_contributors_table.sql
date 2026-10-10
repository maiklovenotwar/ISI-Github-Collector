-- Migration: Add repository_contributors table
-- Date: 2026-08-08
-- Description: Contributor-Netzwerk pro Repository (siehe
--   docs/methodology/contributor_network.md) — wer hat woran mitgewirkt, mit
--   Commit-Anteil. Ergänzt die bisherige Einzel-Owner-Beziehung auf
--   `repositories.owner_id` um die volle Contributor-Liste je Repo, Grundlage
--   für grenzüberschreitende Ko-Contribution-Indikatoren.
--
-- contributor_id ist absichtlich NULLable: anonyme Contributors (E-Mail-Autoren
--   ohne GitHub-Account, `anon=true`) und Logins, die noch nicht in
--   `contributors` erfasst sind, werden trotzdem über github_login gespeichert.
--
-- Check current schema before running:
-- SHOW TABLES LIKE 'repository_contributors';

CREATE TABLE IF NOT EXISTS repository_contributors (
    id INT PRIMARY KEY AUTO_INCREMENT,
    repository_id INT NOT NULL COMMENT 'FK zu repositories.id',
    contributor_id INT DEFAULT NULL COMMENT 'FK zu contributors.id, NULL wenn (noch) nicht lokal erfasst',
    github_login VARCHAR(255) NOT NULL COMMENT 'GitHub-Login des Contributors',
    contributions INT DEFAULT NULL COMMENT 'Commit-Anzahl auf dem Default-Branch laut GitHub API',
    is_anonymous BOOLEAN NOT NULL DEFAULT FALSE COMMENT 'E-Mail-Autor ohne verknuepften GitHub-Account',
    collected_at DATETIME NOT NULL COMMENT 'Zeitpunkt der Sammlung',
    run_id VARCHAR(36) DEFAULT NULL COMMENT 'UUID des Sammel-Laufs (fuer Revert)',

    FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
    FOREIGN KEY (contributor_id) REFERENCES contributors(id) ON DELETE SET NULL,
    INDEX idx_repository_contributors_repository_id (repository_id),
    INDEX idx_repository_contributors_contributor_id (contributor_id),
    INDEX idx_repository_contributors_run_id (run_id),
    UNIQUE KEY uk_repo_contributor_login (repository_id, github_login)
) COMMENT 'Contributor-Netzwerk pro Repository (siehe docs/methodology/contributor_network.md)';

-- Rollback eines kompletten Laufs:
-- DELETE FROM repository_contributors WHERE run_id = '<run_id>';

-- Verify migration:
-- DESCRIBE repository_contributors;
-- SELECT COUNT(*) FROM repository_contributors;
