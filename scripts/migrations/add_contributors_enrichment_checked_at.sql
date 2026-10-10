-- Migration: Add enrichment_checked_at to contributors
-- Date: 2026-08-27
-- Description: Die Profil-Anreicherung des Contributor-Netzwerks hat bislang "noch nicht
--   angereichert" ueber `name IS NULL AND location IS NULL AND company IS NULL`
--   erkannt. Viele echte GitHub-Profile haben aber alle drei Felder leer (z.B.
--   'technoweenie') - die wurden bei jedem erneuten Lauf wieder als offen
--   ausgewaehlt und erneut per GraphQL abgefragt, unabhaengig davon wie oft das
--   Skript schon lief. Dieser Zeitstempel wird bei JEDEM Profil-Abruf gesetzt
--   (auch wenn GitHub keine Daten zurueckgibt), unabhaengig vom Inhalt der
--   anderen Felder, und ersetzt die Feld-Heuristik als Auswahlkriterium.
--
-- Check current schema before running:
-- DESCRIBE contributors;

ALTER TABLE contributors
    ADD COLUMN enrichment_checked_at DATETIME DEFAULT NULL
    COMMENT 'Zeitpunkt des letzten Profil-Abrufversuchs (auch bei leerem Ergebnis gesetzt)';

ALTER TABLE contributors
    ADD INDEX idx_contributors_enrichment_checked_at (enrichment_checked_at);

-- Backfill: markiert rueckwirkend alles, wofuer irgendein Profil-Zaehlfeld gesetzt ist
-- (followers/public_repos/public_gists/following) als "geprueft" - sonst wuerde der naechste
-- Lauf sofort wieder alle 8,8 Mio. bereits bekannten Contributors neu abfragen.
--
-- WICHTIGE EINSCHRAENKUNG: Rueckwirkend NICHT unterscheidbar sind "per GraphQL geprueft, Profil
-- war leer" (z.B. 'technoweenie') von "vor Jahren per REST beim urspruenglichen Repo-Sammeln
-- erfasst, Profil ist einfach leer" - beide Faelle sehen in den Daten identisch aus, es gibt
-- keine Spalte, die festhaelt, WANN/WOMIT eine Zeile zuletzt angefasst wurde. Konsequenz: ca.
-- 2,86 Mio. Contributors mit leerem Profil, die nie durch die GraphQL-Anreicherung liefen,
-- werden hier faelschlich als "geprueft" markiert und dadurch nie erneut versucht. Vertretbar,
-- weil REST und GraphQL dieselben zugrunde liegenden Profildaten lesen - ein erneuter Versuch
-- haette in den allermeisten Faellen ohnehin nichts Neues gefunden. Reversibel bei Bedarf:
-- UPDATE contributors SET enrichment_checked_at = NULL WHERE ...
UPDATE contributors
SET enrichment_checked_at = NOW()
WHERE enrichment_checked_at IS NULL
  AND (followers IS NOT NULL OR public_repos IS NOT NULL
       OR public_gists IS NOT NULL OR following IS NOT NULL);

-- Verify migration:
-- DESCRIBE contributors;
-- SELECT COUNT(*) FROM contributors WHERE enrichment_checked_at IS NULL;
