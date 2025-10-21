-- ============================================================
-- CryptoCompare News Table (cc_news)
-- Stores hourly crypto news articles with full body text
-- ============================================================

-- Drop table if exists (CAUTION: This will delete all data!)
-- DROP TABLE IF EXISTS cc_news;

-- Create main table
CREATE TABLE IF NOT EXISTS cc_news (
    -- Primary identifiers
    id BIGINT PRIMARY KEY,
    title TEXT NOT NULL,

    -- Timestamps
    published_on TIMESTAMP WITH TIME ZONE NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Source information
    source VARCHAR(100),
    source_name VARCHAR(255),

    -- Content
    url TEXT UNIQUE NOT NULL,
    body TEXT,
    body_length INTEGER,

    -- Metadata
    categories TEXT,
    tags TEXT,
    lang VARCHAR(10) DEFAULT 'EN',

    -- Media
    has_image BOOLEAN DEFAULT FALSE,
    imageurl TEXT,

    -- Engagement
    upvotes INTEGER DEFAULT 0,
    downvotes INTEGER DEFAULT 0
);

-- ============================================================
-- Indexes for Performance
-- ============================================================

-- Date-based queries (most common)
CREATE INDEX IF NOT EXISTS idx_cc_news_published_on
ON cc_news(published_on DESC);

-- Fetch tracking
CREATE INDEX IF NOT EXISTS idx_cc_news_fetched_at
ON cc_news(fetched_at DESC);

-- Source filtering
CREATE INDEX IF NOT EXISTS idx_cc_news_source
ON cc_news(source);

-- Category filtering (GIN index for array operations)
CREATE INDEX IF NOT EXISTS idx_cc_news_categories
ON cc_news USING gin(string_to_array(categories, '|'));

-- Full-text search on body (optional, can be slow on large tables)
-- CREATE INDEX IF NOT EXISTS idx_cc_news_body_fts
-- ON cc_news USING gin(to_tsvector('english', body));

-- ============================================================
-- Table Comments
-- ============================================================

COMMENT ON TABLE cc_news IS 'CryptoCompare cryptocurrency news articles - fetched hourly via GitHub Actions';

COMMENT ON COLUMN cc_news.id IS 'Unique article ID from CryptoCompare API';
COMMENT ON COLUMN cc_news.title IS 'Article headline/title';
COMMENT ON COLUMN cc_news.published_on IS 'Original publication timestamp from source';
COMMENT ON COLUMN cc_news.fetched_at IS 'When we fetched this article';
COMMENT ON COLUMN cc_news.source IS 'Source key/slug (e.g., coinotag, newsbtc)';
COMMENT ON COLUMN cc_news.source_name IS 'Human-readable source name (e.g., CoinOtag, NewsBTC)';
COMMENT ON COLUMN cc_news.url IS 'Article URL (unique constraint prevents duplicates)';
COMMENT ON COLUMN cc_news.body IS 'Full article body text';
COMMENT ON COLUMN cc_news.body_length IS 'Character count of body text';
COMMENT ON COLUMN cc_news.categories IS 'Pipe-separated categories (e.g., BTC|TRADING|MARKET)';
COMMENT ON COLUMN cc_news.tags IS 'Pipe-separated tags (e.g., Bitcoin|News|Analysis)';

-- ============================================================
-- Verify Installation
-- ============================================================

-- Check table structure
SELECT
    column_name,
    data_type,
    character_maximum_length,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'cc_news'
ORDER BY ordinal_position;

-- Check indexes
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'cc_news';

-- Show table size
SELECT pg_size_pretty(pg_total_relation_size('cc_news')) AS total_size;
