# 🕐 Hourly Crypto News Fetch - Setup Guide

This document explains the automated hourly crypto news fetching system that pulls articles from CryptoCompare API and stores them in PostgreSQL database.

---

## 📋 System Overview

### What It Does
- ✅ Fetches crypto news articles from **last 1 hour** using timestamp-based filtering
- ✅ Stores articles in **PostgreSQL database** (`cc_news` table)
- ✅ Exports latest data to **CSV and JSON** formats
- ✅ Cleans up old export files (keeps only latest)
- ✅ Runs automatically **every hour** via GitHub Actions
- ✅ Handles duplicates automatically (based on unique URL)

### Architecture
```
CryptoCompare API → fetch_hourly.py → PostgreSQL (cc_news table)
                                   → CSV/JSON exports (latest only)
```

---

## 🗄️ Database Schema

### Table: `cc_news`

```sql
CREATE TABLE cc_news (
    id BIGINT PRIMARY KEY,                      -- Article ID from CryptoCompare
    title TEXT NOT NULL,                         -- Article headline
    published_on TIMESTAMP WITH TIME ZONE,       -- Original publication time
    source VARCHAR(100),                         -- Source key (e.g., 'coinotag')
    source_name VARCHAR(255),                    -- Human-readable source name
    url TEXT UNIQUE NOT NULL,                    -- Article URL (unique constraint)
    categories TEXT,                             -- Pipe-separated categories
    tags TEXT,                                   -- Pipe-separated tags
    lang VARCHAR(10),                            -- Language code (e.g., 'EN')
    body TEXT,                                   -- Full article body text
    body_length INTEGER,                         -- Character count of body
    has_image BOOLEAN,                           -- Whether article has image
    imageurl TEXT,                               -- Image URL
    upvotes INTEGER DEFAULT 0,                   -- Community upvotes
    downvotes INTEGER DEFAULT 0,                 -- Community downvotes
    fetched_at TIMESTAMP WITH TIME ZONE,         -- When we fetched it
    created_at TIMESTAMP WITH TIME ZONE          -- Row creation time
);

-- Indexes for performance
CREATE INDEX idx_cc_news_published_on ON cc_news(published_on DESC);
CREATE INDEX idx_cc_news_source ON cc_news(source);
CREATE INDEX idx_cc_news_categories ON cc_news USING gin(string_to_array(categories, '|'));
CREATE INDEX idx_cc_news_fetched_at ON cc_news(fetched_at DESC);
```

### Key Features
- **Primary Key:** Article ID ensures no duplicate articles
- **Unique URL:** Prevents duplicate insertions automatically
- **Indexed Timestamps:** Fast queries by date range
- **GIN Index on Categories:** Efficient category filtering
- **Full Body Text:** Complete article content stored

---

## 🚀 Setup Instructions

### 1. Database Setup

#### Create Table
```bash
python -m src.news_fetcher.db_cc_news
```

This will:
- Test database connection
- Create `cc_news` table if it doesn't exist
- Create all indexes
- Show current table statistics

#### Manual Table Creation
If you prefer SQL:
```bash
psql -h YOUR_HOST -U YOUR_USER -d YOUR_DB -f sql/create_cc_news_table.sql
```

### 2. Environment Variables

Ensure your `.env` file has:
```env
CRYPTOCOMPARE_API_KEY=your_api_key_here
DB_HOST=your_db_host
DB_PORT=5432
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=your_db_name
```

### 3. GitHub Actions Setup

#### Required Secrets
Add these secrets to your GitHub repository:
- Settings → Secrets and variables → Actions → New repository secret

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `CRYPTOCOMPARE_API_KEY` | Your CryptoCompare API key | `abc123...` |
| `DB_HOST` | PostgreSQL host | `34.55.195.199` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_USER` | Database username | `yogass09` |
| `DB_PASSWORD` | Database password | `your_password` |
| `DB_NAME` | Database name | `dbcp` |

#### Enable Workflow
1. Go to **Actions** tab in GitHub
2. Enable workflows if disabled
3. The workflow runs automatically every hour
4. Manual trigger: Actions → "Hourly Crypto News Fetch" → "Run workflow"

---

## 📊 Usage

### Manual Run (Local)
```bash
# Fetch last hour and push to DB
python -m src.news_fetcher.fetch_hourly
```

### Check Database Statistics
```python
from src.news_fetcher import db_cc_news

# Get table stats
stats = db_cc_news.get_table_stats()
print(f"Total articles: {stats['total_articles']}")
print(f"Date range: {stats['oldest_article']} to {stats['newest_article']}")

# Get latest 10 articles
latest = db_cc_news.get_latest_articles(limit=10)
for article in latest:
    print(article)
```

### Query Examples

#### Get latest articles
```sql
SELECT id, title, published_on, source_name, url
FROM cc_news
ORDER BY published_on DESC
LIMIT 10;
```

#### Filter by source
```sql
SELECT COUNT(*), source_name
FROM cc_news
GROUP BY source_name
ORDER BY COUNT(*) DESC;
```

#### Filter by category (Bitcoin articles)
```sql
SELECT id, title, published_on, categories
FROM cc_news
WHERE categories LIKE '%BTC%'
ORDER BY published_on DESC
LIMIT 20;
```

#### Articles from last 24 hours
```sql
SELECT COUNT(*) as count_24h
FROM cc_news
WHERE published_on >= NOW() - INTERVAL '24 hours';
```

#### Body text search
```sql
SELECT id, title, published_on
FROM cc_news
WHERE body ILIKE '%ethereum%'
ORDER BY published_on DESC
LIMIT 10;
```

---

## 📁 File Structure

### Export Files (Auto-Cleaned)
- `data/csv_exports/crypto_news_hourly_YYYYMMDD_HHMMSS.csv` - Latest CSV (old ones deleted)
- `data/json_exports/crypto_news_hourly_YYYYMMDD_HHMMSS.json` - Latest JSON (old ones deleted)
- `data/analysis/analysis_hourly_YYYYMMDD_HHMMSS.json` - Latest analysis (old ones deleted)

**Note:** Only the **latest** file is kept. Old files are automatically cleaned up after each run.

### CSV Structure (15 columns)
```
id, title, published_on, source, url, categories, tags, lang,
source_name, body, body_length, has_image, imageurl, upvotes, downvotes
```

---

## 🔄 Workflow Details

### GitHub Actions Schedule
- **Cron:** `0 * * * *` (every hour at :00 minutes)
- **Timezone:** UTC
- **Manual Trigger:** Available via workflow_dispatch

### Execution Flow
1. **Fetch** - Pull articles from last hour via CryptoCompare API
2. **Export** - Save to CSV/JSON files
3. **Analyze** - Generate statistics
4. **Database** - Insert into PostgreSQL `cc_news` table
5. **Cleanup** - Delete old CSV/JSON files (keep only latest)
6. **Upload** - Store artifacts in GitHub (7-day retention)
7. **Commit** - Push latest exports to repo (optional)

### Duplicate Handling
- Articles are identified by **unique URL**
- Duplicate insertions are automatically **skipped** via `ON CONFLICT (url) DO NOTHING`
- Database reports: `New articles inserted` vs `Duplicates skipped`

---

## 📊 Expected Results

### Typical Hourly Fetch
- **Articles per hour:** 50-70 articles
- **Unique sources:** 15-25 sources
- **Database growth:** ~50-70 new rows/hour
- **Duplicates:** Usually 0-5 per run (overlapping fetches)
- **File cleanup:** 3 old files deleted per run

### Daily Stats
- **~1,200-1,680 articles/day** (24 hours × 50-70 articles)
- **~500KB CSV size** per export
- **100% image coverage** (all articles have images)

---

## 🐛 Troubleshooting

### Database Connection Failed
```bash
# Test connection manually
python -m src.news_fetcher.db_cc_news
```

**Fix:**
- Verify `.env` credentials
- Check database firewall rules
- Ensure PostgreSQL is running

### No Articles Fetched
**Possible causes:**
- API key invalid or rate-limited
- No new articles published in last hour (rare)
- Network connectivity issues

**Fix:**
```bash
# Check API key
echo $CRYPTOCOMPARE_API_KEY

# Test API manually
curl "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key=YOUR_KEY"
```

### GitHub Actions Failing
**Check:**
1. Secrets are set correctly (no spaces, correct values)
2. Repository has Actions enabled
3. Workflow file syntax is valid
4. Check Actions logs for specific errors

---

## 🔧 Maintenance

### Database Maintenance
```sql
-- Check table size
SELECT pg_size_pretty(pg_total_relation_size('cc_news'));

-- Vacuum table (reclaim space)
VACUUM ANALYZE cc_news;

-- Reindex for performance
REINDEX TABLE cc_news;
```

### Monitoring Queries
```sql
-- Articles added in last 24 hours
SELECT COUNT(*) FROM cc_news
WHERE fetched_at >= NOW() - INTERVAL '24 hours';

-- Top sources today
SELECT source_name, COUNT(*)
FROM cc_news
WHERE fetched_at >= CURRENT_DATE
GROUP BY source_name
ORDER BY COUNT(*) DESC;

-- Check for gaps (hours with no fetches)
SELECT generate_series(
    date_trunc('hour', MIN(fetched_at)),
    date_trunc('hour', MAX(fetched_at)),
    '1 hour'::interval
) AS hour
FROM cc_news
EXCEPT
SELECT DISTINCT date_trunc('hour', fetched_at)
FROM cc_news
ORDER BY hour;
```

---

## 📈 Performance Tips

1. **Indexes are crucial** - Don't remove them
2. **Regular VACUUM** - Run weekly for optimal performance
3. **Partition by month** - Consider for tables > 1M rows
4. **Archive old data** - Move articles older than 6 months to archive table

---

## 🎯 Next Steps

### Possible Enhancements
- [ ] Add sentiment analysis on body text
- [ ] Create alerting for specific keywords
- [ ] Build real-time dashboard
- [ ] Add data quality checks
- [ ] Implement retry logic for API failures
- [ ] Add Slack/Discord notifications for important news

---

## 📞 Support

For issues or questions:
1. Check logs: `python -m src.news_fetcher.fetch_hourly`
2. Test DB: `python -m src.news_fetcher.db_cc_news`
3. Review GitHub Actions logs
4. Check [CHANGELOG.md](CHANGELOG.md) for recent changes

---

**Last Updated:** October 21, 2025
