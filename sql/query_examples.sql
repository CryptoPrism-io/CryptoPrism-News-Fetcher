-- ============================================================
-- cc_news Table - Useful Query Examples
-- ============================================================

-- ============================================================
-- Basic Queries
-- ============================================================

-- Get latest 10 articles
SELECT
    id,
    title,
    published_on,
    source_name,
    url
FROM cc_news
ORDER BY published_on DESC
LIMIT 10;

-- Count total articles
SELECT COUNT(*) AS total_articles FROM cc_news;

-- Get date range
SELECT
    MIN(published_on) AS oldest_article,
    MAX(published_on) AS newest_article,
    AGE(MAX(published_on), MIN(published_on)) AS time_span
FROM cc_news;

-- ============================================================
-- Time-Based Queries
-- ============================================================

-- Articles from last 24 hours
SELECT COUNT(*) AS articles_last_24h
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '24 hours';

-- Articles per hour (last 24 hours)
SELECT
    DATE_TRUNC('hour', published_on) AS hour,
    COUNT(*) AS article_count
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '24 hours'
GROUP BY DATE_TRUNC('hour', published_on)
ORDER BY hour DESC;

-- Articles per day (last 7 days)
SELECT
    DATE(published_on) AS day,
    COUNT(*) AS article_count
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '7 days'
GROUP BY DATE(published_on)
ORDER BY day DESC;

-- Articles fetched today
SELECT COUNT(*) AS fetched_today
FROM cc_news
WHERE fetched_at >= CURRENT_DATE;

-- ============================================================
-- Source Analysis
-- ============================================================

-- Top 10 news sources (all time)
SELECT
    source_name,
    COUNT(*) AS article_count
FROM cc_news
GROUP BY source_name
ORDER BY article_count DESC
LIMIT 10;

-- Top sources in last 24 hours
SELECT
    source_name,
    COUNT(*) AS article_count
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '24 hours'
GROUP BY source_name
ORDER BY article_count DESC;

-- Articles per source with average body length
SELECT
    source_name,
    COUNT(*) AS total_articles,
    AVG(body_length) AS avg_body_length,
    MIN(body_length) AS min_length,
    MAX(body_length) AS max_length
FROM cc_news
GROUP BY source_name
ORDER BY total_articles DESC;

-- ============================================================
-- Category Analysis
-- ============================================================

-- Most common categories (requires unnesting pipe-separated values)
SELECT
    UNNEST(string_to_array(categories, '|')) AS category,
    COUNT(*) AS frequency
FROM cc_news
WHERE categories IS NOT NULL AND categories != ''
GROUP BY category
ORDER BY frequency DESC
LIMIT 20;

-- Bitcoin-related articles
SELECT
    id,
    title,
    published_on,
    categories
FROM cc_news
WHERE categories LIKE '%BTC%'
ORDER BY published_on DESC
LIMIT 20;

-- Ethereum-related articles
SELECT
    id,
    title,
    published_on,
    categories
FROM cc_news
WHERE categories LIKE '%ETH%'
ORDER BY published_on DESC
LIMIT 20;

-- Articles with multiple specific categories
SELECT
    id,
    title,
    published_on,
    categories
FROM cc_news
WHERE categories LIKE '%BTC%'
  AND categories LIKE '%TRADING%'
ORDER BY published_on DESC;

-- ============================================================
-- Content Analysis
-- ============================================================

-- Articles with longest bodies
SELECT
    id,
    title,
    source_name,
    body_length,
    published_on
FROM cc_news
ORDER BY body_length DESC
LIMIT 10;

-- Average body length by source
SELECT
    source_name,
    COUNT(*) AS article_count,
    ROUND(AVG(body_length)) AS avg_length
FROM cc_news
GROUP BY source_name
HAVING COUNT(*) >= 10
ORDER BY avg_length DESC;

-- Articles without body text
SELECT
    id,
    title,
    source_name,
    url
FROM cc_news
WHERE body IS NULL OR body = '' OR body_length = 0;

-- ============================================================
-- Full-Text Search
-- ============================================================

-- Search for keyword in title
SELECT
    id,
    title,
    published_on,
    source_name
FROM cc_news
WHERE title ILIKE '%ethereum%'
ORDER BY published_on DESC
LIMIT 20;

-- Search for keyword in body text
SELECT
    id,
    title,
    published_on,
    LEFT(body, 200) AS preview
FROM cc_news
WHERE body ILIKE '%decentralized%'
ORDER BY published_on DESC
LIMIT 10;

-- Search in both title and body
SELECT
    id,
    title,
    published_on,
    CASE
        WHEN title ILIKE '%bitcoin%' THEN 'title'
        WHEN body ILIKE '%bitcoin%' THEN 'body'
        ELSE 'both'
    END AS found_in
FROM cc_news
WHERE title ILIKE '%bitcoin%' OR body ILIKE '%bitcoin%'
ORDER BY published_on DESC
LIMIT 20;

-- ============================================================
-- Engagement & Media
-- ============================================================

-- Articles with most upvotes
SELECT
    id,
    title,
    upvotes,
    downvotes,
    (upvotes - downvotes) AS net_votes,
    published_on
FROM cc_news
WHERE upvotes > 0 OR downvotes > 0
ORDER BY net_votes DESC
LIMIT 10;

-- Articles with images vs without
SELECT
    has_image,
    COUNT(*) AS article_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) AS percentage
FROM cc_news
GROUP BY has_image;

-- ============================================================
-- Duplicate Detection
-- ============================================================

-- Find duplicate URLs (should be 0 due to UNIQUE constraint)
SELECT
    url,
    COUNT(*) AS duplicate_count
FROM cc_news
GROUP BY url
HAVING COUNT(*) > 1;

-- Find similar titles (potential duplicates)
SELECT
    a.id AS id1,
    b.id AS id2,
    a.title,
    a.published_on AS date1,
    b.published_on AS date2
FROM cc_news a
JOIN cc_news b ON a.title = b.title
WHERE a.id < b.id
ORDER BY a.published_on DESC;

-- ============================================================
-- Data Quality Checks
-- ============================================================

-- Articles with missing critical fields
SELECT
    'Missing Title' AS issue,
    COUNT(*) AS count
FROM cc_news
WHERE title IS NULL OR title = ''
UNION ALL
SELECT
    'Missing URL',
    COUNT(*)
FROM cc_news
WHERE url IS NULL OR url = ''
UNION ALL
SELECT
    'Missing Source',
    COUNT(*)
FROM cc_news
WHERE source IS NULL OR source = ''
UNION ALL
SELECT
    'Future Published Date',
    COUNT(*)
FROM cc_news
WHERE published_on > NOW();

-- Check for gaps in hourly fetches
SELECT
    hour,
    'No data' AS status
FROM generate_series(
    DATE_TRUNC('hour', (SELECT MIN(fetched_at) FROM cc_news)),
    DATE_TRUNC('hour', NOW()),
    '1 hour'::INTERVAL
) hour
WHERE NOT EXISTS (
    SELECT 1
    FROM cc_news
    WHERE DATE_TRUNC('hour', fetched_at) = hour
)
ORDER BY hour DESC
LIMIT 20;

-- ============================================================
-- Performance & Maintenance
-- ============================================================

-- Table size and index sizes
SELECT
    'cc_news table' AS object,
    pg_size_pretty(pg_total_relation_size('cc_news')) AS size
UNION ALL
SELECT
    'Table data only',
    pg_size_pretty(pg_relation_size('cc_news'))
UNION ALL
SELECT
    'Indexes total',
    pg_size_pretty(pg_indexes_size('cc_news'));

-- Rows per index page (check index efficiency)
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan AS index_scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE tablename = 'cc_news';

-- Dead tuples (indicates need for VACUUM)
SELECT
    schemaname,
    tablename,
    n_live_tup AS live_rows,
    n_dead_tup AS dead_rows,
    ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct
FROM pg_stat_user_tables
WHERE tablename = 'cc_news';

-- ============================================================
-- Trending & Analytics
-- ============================================================

-- Trending topics (categories in last 24h)
SELECT
    UNNEST(string_to_array(categories, '|')) AS category,
    COUNT(*) AS mentions_24h
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '24 hours'
  AND categories IS NOT NULL
GROUP BY category
ORDER BY mentions_24h DESC
LIMIT 15;

-- Source activity comparison (today vs yesterday)
WITH today AS (
    SELECT source_name, COUNT(*) AS count
    FROM cc_news
    WHERE published_on >= CURRENT_DATE
    GROUP BY source_name
),
yesterday AS (
    SELECT source_name, COUNT(*) AS count
    FROM cc_news
    WHERE published_on >= CURRENT_DATE - INTERVAL '1 day'
      AND published_on < CURRENT_DATE
    GROUP BY source_name
)
SELECT
    COALESCE(t.source_name, y.source_name) AS source,
    COALESCE(y.count, 0) AS yesterday,
    COALESCE(t.count, 0) AS today,
    COALESCE(t.count, 0) - COALESCE(y.count, 0) AS change
FROM today t
FULL OUTER JOIN yesterday y ON t.source_name = y.source_name
ORDER BY today DESC, yesterday DESC;

-- Hourly article volume trend (last 48 hours)
SELECT
    DATE_TRUNC('hour', published_on) AS hour,
    COUNT(*) AS articles,
    AVG(body_length) AS avg_length
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '48 hours'
GROUP BY DATE_TRUNC('hour', published_on)
ORDER BY hour DESC;
