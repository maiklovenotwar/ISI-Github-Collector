-- Read-only queries that support the figures and statements in docs/methodology/.
--
-- Every statement is a SELECT; nothing is changed. The statements run on MySQL and on
-- SQLite. Some scan a whole table and can take minutes on a large database.
--
-- Run them with the client of your choice, for example:
--   mysql --host=... --user=... --password github_data < scripts/diagnostics/methodology_checks.sql

-- 1. Reference date of the collection (repository_collection.md, "Reference date").
--    Each repository carries the time of its last retrieval; the collection is a snapshot
--    spread over the period between the two dates.
SELECT COUNT(*) AS repositories,
       MIN(collected_at) AS first_retrieval,
       MAX(collected_at) AS last_retrieval,
       SUM(CASE WHEN collected_at IS NULL THEN 1 ELSE 0 END) AS without_retrieval_time
FROM repositories;

-- 2. Retrievals per month (same section).
SELECT SUBSTR(CAST(collected_at AS CHAR), 1, 7) AS retrieval_month, COUNT(*) AS repositories
FROM repositories
GROUP BY SUBSTR(CAST(collected_at AS CHAR), 1, 7)
ORDER BY retrieval_month;

-- 3. Repositories per month of creation (repository_collection.md, "Completeness").
--    A month that is markedly lower than its neighbours indicates a gap in the collection.
SELECT SUBSTR(CAST(created_at AS CHAR), 1, 7) AS creation_month, COUNT(*) AS repositories
FROM repositories
GROUP BY SUBSTR(CAST(created_at AS CHAR), 1, 7)
ORDER BY creation_month;

-- 4. Forks in the collection (repository_collection.md, "Population").
--    Expected: zero, because the search query does not request forks.
SELECT SUM(CASE WHEN fork = 1 THEN 1 ELSE 0 END) AS forks, COUNT(*) AS repositories
FROM repositories;

-- 5. Accounts by type, with the share that states a location and the share with a country
--    (geocoding.md, "Input"; country_attribution.md, "Unit of analysis").
--    country_code = '' marks a location for which the geocoder returned no match.
SELECT type,
       COUNT(*) AS accounts,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' THEN 1 ELSE 0 END) AS with_location,
       SUM(CASE WHEN country_code IS NOT NULL AND country_code <> '' THEN 1 ELSE 0 END) AS with_country,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' AND country_code = '' THEN 1 ELSE 0 END)
           AS location_marked_not_geocodable,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' AND country_code IS NULL THEN 1 ELSE 0 END)
           AS location_not_yet_processed
FROM contributors
GROUP BY type;

-- 6. The same for organizations.
SELECT COUNT(*) AS organizations,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' THEN 1 ELSE 0 END) AS with_location,
       SUM(CASE WHEN country_code IS NOT NULL AND country_code <> '' THEN 1 ELSE 0 END) AS with_country,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' AND country_code = '' THEN 1 ELSE 0 END)
           AS location_marked_not_geocodable,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' AND country_code IS NULL THEN 1 ELSE 0 END)
           AS location_not_yet_processed
FROM organizations;

-- 7. Organizations and their placeholder row in contributors (country_attribution.md,
--    "Unit of analysis"). An organization that owns a repository has a row in both tables
--    with the same GitHub id. The rule uses the row in organizations; this query shows how
--    often the two rows would give a different answer.
SELECT COUNT(*) AS organizations_with_placeholder,
       SUM(CASE WHEN COALESCE(o.country_code, '') = COALESCE(c.country_code, '') THEN 1 ELSE 0 END)
           AS same_country_or_both_empty,
       SUM(CASE WHEN COALESCE(o.country_code, '') <> '' AND COALESCE(c.country_code, '') <> ''
                 AND o.country_code <> c.country_code THEN 1 ELSE 0 END) AS different_countries,
       SUM(CASE WHEN COALESCE(o.country_code, '') <> '' AND COALESCE(c.country_code, '') = '' THEN 1 ELSE 0 END)
           AS only_organization_row_has_country,
       SUM(CASE WHEN COALESCE(o.country_code, '') = '' AND COALESCE(c.country_code, '') <> '' THEN 1 ELSE 0 END)
           AS only_placeholder_row_has_country
FROM organizations o
JOIN contributors c ON c.id = o.id;

-- 8. Country of a repository by the documented rule (country_attribution.md, "Country of a
--    repository"): the organization's country if the repository belongs to an organization,
--    otherwise the country of the owning account. Only the geocoded location is used.
SELECT CASE WHEN r.organization_id IS NOT NULL THEN 'organization' ELSE 'user' END AS owner_kind,
       COUNT(*) AS repositories,
       SUM(CASE WHEN COALESCE(CASE WHEN r.organization_id IS NOT NULL THEN o.country_code
                                   ELSE c.country_code END, '') <> '' THEN 1 ELSE 0 END)
           AS with_country
FROM repositories r
LEFT JOIN organizations o ON o.id = r.organization_id
LEFT JOIN contributors c ON c.id = r.owner_id
GROUP BY CASE WHEN r.organization_id IS NOT NULL THEN 'organization' ELSE 'user' END;

-- 9. Countries assigned by the ccTLD step, organizations (country_attribution.md, "ccTLD as
--    a sensitivity analysis"). Generic domains such as .com and .io yield no country, so
--    the United States can hardly appear here.
SELECT country_code_cctld, COUNT(*) AS organizations
FROM organizations
WHERE country_code_cctld IS NOT NULL AND country_code_cctld <> ''
GROUP BY country_code_cctld
ORDER BY organizations DESC
LIMIT 15;

-- 10. The same for accounts in contributors.
SELECT country_code_cctld, COUNT(*) AS accounts
FROM contributors
WHERE country_code_cctld IS NOT NULL AND country_code_cctld <> ''
GROUP BY country_code_cctld
ORDER BY accounts DESC
LIMIT 15;

-- 11. Countries from the geocoded location, organizations, for comparison with query 9.
SELECT country_code, COUNT(*) AS organizations
FROM organizations
WHERE country_code IS NOT NULL AND country_code <> ''
GROUP BY country_code
ORDER BY organizations DESC
LIMIT 15;
