-- Read-only queries that support the figures and statements in docs/methodology/.
--
-- Every statement is a SELECT; nothing is changed. The statements run on MySQL; the tests
-- run them on SQLite, where YEAR() and MONTH() are registered as functions.
--
-- The table repositories (27.5 million rows, about 14 GB with indexes on 2026-10-14) is read
-- in full by two statements only: query 1 without a join and query 6 with the owner's rows.
-- Query 2 reads only the index on collected_at. Run times are from the project database
-- with a buffer pool of 1 GB (innodb_buffer_pool_size); a larger buffer shortens them.
--
-- Run them with the client of your choice, for example:
--   mysql --host=... --user=... --password github_data < scripts/diagnostics/methodology_checks.sql

-- 1. Repositories per month of creation, with the counts that need a full read of the table
--    (repository_collection.md, "Population", "Completeness", "Fields Not Collected").
--    Grouped by numbers: grouping by a text form of the date exceeded the temporary table
--    limit of the server (MySQL 8.0.23) on 2026-10-13. The totals are the sums over all rows.
--    - A month that is markedly lower than its neighbours indicates a gap in the collection.
--    - forks: expected zero, because the search query does not request forks.
--    - without_retrieval_time: stored before collected_at was introduced.
--    Run time: it replaces five separate full reads, which together with the first join
--    query took about 20 minutes on 2026-10-13/14 (the whole round about 2 hours, of which
--    100 minutes for the second join query, see query 6).
SELECT YEAR(created_at) AS creation_year,
       MONTH(created_at) AS creation_month,
       COUNT(*) AS repositories,
       SUM(CASE WHEN fork = 1 THEN 1 ELSE 0 END) AS forks,
       SUM(CASE WHEN stargazers_count < 5 THEN 1 ELSE 0 END) AS stars_below_5,
       SUM(CASE WHEN stargazers_count >= 5 AND stargazers_count < 10 THEN 1 ELSE 0 END)
           AS stars_5_to_9,
       SUM(CASE WHEN stargazers_count >= 10 THEN 1 ELSE 0 END) AS stars_10_or_more,
       SUM(CASE WHEN commits_count IS NOT NULL AND pull_requests_count IS NOT NULL THEN 1 ELSE 0 END)
           AS with_statistics,
       SUM(CASE WHEN collected_at IS NULL THEN 1 ELSE 0 END) AS without_retrieval_time,
       SUM(CASE WHEN contributors_count IS NOT NULL THEN 1 ELSE 0 END)
           AS with_contributors_count
FROM repositories
GROUP BY YEAR(created_at), MONTH(created_at)
ORDER BY creation_year, creation_month;

-- 2. Retrievals per month (repository_collection.md, "Reference Date"). Reads only the index
--    on collected_at. The first and last month give the period of the collection.
SELECT YEAR(collected_at) AS retrieval_year, MONTH(collected_at) AS retrieval_month,
       COUNT(*) AS repositories
FROM repositories
WHERE collected_at IS NOT NULL
GROUP BY YEAR(collected_at), MONTH(collected_at)
ORDER BY retrieval_year, retrieval_month;

-- 3. Accounts by type, with the share that states a location and the share with a country
--    (geocoding.md, "Input"; country_attribution.md, "Unit of analysis").
--    country_code = '' marks a row that was processed without a result: the string is
--    not a place, the geocoder returned no match or an invalid code, or (before
--    2026-10-12) the geocoder did not answer. Reads contributors once (about 3 minutes).
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

-- 4. The same for organizations (seconds).
SELECT COUNT(*) AS organizations,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' THEN 1 ELSE 0 END) AS with_location,
       SUM(CASE WHEN country_code IS NOT NULL AND country_code <> '' THEN 1 ELSE 0 END) AS with_country,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' AND country_code = '' THEN 1 ELSE 0 END)
           AS location_marked_not_geocodable,
       SUM(CASE WHEN location IS NOT NULL AND location <> '' AND country_code IS NULL THEN 1 ELSE 0 END)
           AS location_not_yet_processed
FROM organizations;

-- 5. Organizations and their placeholder row in contributors (country_attribution.md,
--    "Unit of analysis"). An organization that owns a repository has a row in both tables
--    with the same GitHub id. The rule uses the row in organizations; this query shows how
--    often the two rows would give a different answer (seconds).
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

-- 6. Country of a repository by the documented rule, and how the owner is recorded
--    (country_attribution.md, "Country of a repository" and "Unit of analysis").
--    - with_country: if the owner is an organization, the country of its row in
--      organizations, otherwise the country of the owning account. Only the geocoded
--      location is used. The owner's kind is taken from its account type, because
--      repositories.organization_id is not filled in older collections.
--    - with_organization_id: collections made before 2026-10-13 did not set this column
--      for repositories found through the search.
--    - organization_without_row_in_organizations, without_owner_row: expected zero.
--    This is the only statement that joins repositories with the owner tables. Its two
--    predecessors ran for about 5 and about 100 minutes (2026-10-13/14); with a buffer pool
--    of 1 GB, most of that time is spent on single lookups in contributors.
SELECT CASE WHEN c.type = 'Organization' THEN 'organization' ELSE 'user' END AS owner_kind,
       COUNT(*) AS repositories,
       SUM(CASE WHEN COALESCE(CASE WHEN c.type = 'Organization' THEN o.country_code
                                   ELSE c.country_code END, '') <> '' THEN 1 ELSE 0 END)
           AS with_country,
       SUM(CASE WHEN r.organization_id IS NOT NULL THEN 1 ELSE 0 END) AS with_organization_id,
       SUM(CASE WHEN c.type = 'Organization' AND o.id IS NULL THEN 1 ELSE 0 END)
           AS organization_without_row_in_organizations,
       SUM(CASE WHEN c.id IS NULL THEN 1 ELSE 0 END) AS without_owner_row
FROM repositories r
LEFT JOIN contributors c ON c.id = r.owner_id
LEFT JOIN organizations o ON o.id = r.owner_id
GROUP BY CASE WHEN c.type = 'Organization' THEN 'organization' ELSE 'user' END;

-- 7. Countries assigned by the ccTLD step, organizations (country_attribution.md, "ccTLD as
--    a sensitivity analysis"). Generic domains such as .com and .io yield no country, so
--    the United States can hardly appear here (seconds).
SELECT country_code_cctld, COUNT(*) AS organizations
FROM organizations
WHERE country_code_cctld IS NOT NULL AND country_code_cctld <> ''
GROUP BY country_code_cctld
ORDER BY organizations DESC
LIMIT 15;

-- 8. The same for accounts in contributors.
SELECT country_code_cctld, COUNT(*) AS accounts
FROM contributors
WHERE country_code_cctld IS NOT NULL AND country_code_cctld <> ''
GROUP BY country_code_cctld
ORDER BY accounts DESC
LIMIT 15;

-- 9. Countries from the geocoded location, organizations, for comparison with query 7.
SELECT country_code, COUNT(*) AS organizations
FROM organizations
WHERE country_code IS NOT NULL AND country_code <> ''
GROUP BY country_code
ORDER BY organizations DESC
LIMIT 15;
