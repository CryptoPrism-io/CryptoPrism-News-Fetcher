# CoinDesk API CSV Export Summary

## 📊 Export Results

**Export Date**: October 1, 2025
**Export Location**: `data/csv_exports/`

---

## ✅ Successful Exports

### 1. **Sources** (`sources_20251001_145721.csv`)
- **Size**: 8.1 KB
- **Records**: 44 news sources
- **Status**: ✅ Success

#### Columns (14 fields):
```
ID, SOURCE_KEY, NAME, IMAGE_URL, URL, LANG, SOURCE_TYPE,
LAUNCH_DATE, SORT_ORDER, BENCHMARK_SCORE, STATUS,
LAST_UPDATED_TS, CREATED_ON, UPDATED_ON
```

#### Sample Data:
| ID | SOURCE_KEY | NAME | BENCHMARK_SCORE | STATUS |
|----|------------|------|-----------------|--------|
| 5 | coindesk | CoinDesk | 71 | ACTIVE |
| 16 | cointelegraph | CoinTelegraph | 66 | ACTIVE |
| 55 | blockworks | Blockworks | 59 | ACTIVE |
| 27 | cryptopotato | Crypto Potato | 57 | ACTIVE |

#### Key Insights:
- **44 active news sources** available
- Sources ranked by benchmark score (71 is highest - CoinDesk)
- All sources are RSS-based
- English language sources only (LANG=EN)

---

### 2. **Categories** (`categories_20251001_145721.csv`)
- **Size**: 15 KB
- **Records**: 182 categories
- **Status**: ✅ Success

#### Columns (7 fields):
```
ID, NAME, STATUS, CREATED_ON, UPDATED_ON,
INCLUDED_WORDS, INCLUDED_PHRASES
```

#### Sample Data:
| ID | NAME | STATUS | INCLUDED_WORDS | INCLUDED_PHRASES |
|----|------|--------|----------------|------------------|
| 1 | 1INCH | ACTIVE | 1INCH | 1INCH NETWORK |
| 2 | AAVE | ACTIVE | AAVE | AAVE PROTOCOL |
| 3 | ADA | ACTIVE | ADA, Cardano, cardano | - |

#### Key Insights:
- **182 cryptocurrency categories** available
- Includes major tokens: BTC, ETH, ADA, AAVE, etc.
- Categories have keyword filters for automated tagging
- Includes both crypto names and broader topics (DEFI, NFT, MARKET)

---

### 3. **Articles** (`articles_20251001_145721.csv`)
- **Size**: 309 KB
- **Records**: 500 articles
- **Status**: ✅ Success

#### Columns (21 fields):
```
ID, GUID, TITLE, SUBTITLE, AUTHORS, PUBLISHED_ON, URL,
IMAGE_URL, KEYWORDS, LANG, SENTIMENT, UPVOTES, DOWNVOTES,
SCORE, STATUS, SOURCE_KEY, SOURCE_NAME, CATEGORIES,
BODY_LENGTH, CREATED_ON, UPDATED_ON
```

#### Search Terms Used:
- bitcoin (100 articles)
- ethereum (100 articles)
- defi (100 articles)
- markets (100 articles)
- policy (100 articles)

#### Sample Data:
| ID | TITLE | SENTIMENT | SOURCE | CATEGORIES | BODY_LENGTH |
|----|-------|-----------|--------|------------|-------------|
| 52511128 | XRP Futures See Institutional Adoption... | POSITIVE | CoinDesk | CRYPTOCURRENCY, XRP, MARKET | 2847 |
| 52509280 | Traders Eye September Jobs Report... | NEUTRAL | CoinDesk | BTC, MARKET, TRADING | 3421 |

#### Key Insights:
- **500 articles** fetched across 5 search terms
- All articles have **sentiment analysis** (POSITIVE/NEUTRAL/NEGATIVE)
- Articles include **multiple categories** per article
- Average body length: ~3,000 characters
- All articles are from CoinDesk source

---

### 4. **Articles with Full Body** (`articles_full_body_20251001_145721.csv`)
- **Size**: 63 KB
- **Records**: 20 articles (detailed)
- **Status**: ✅ Success

#### Columns (11 fields):
```
ID, TITLE, SUBTITLE, AUTHORS, PUBLISHED_ON, URL,
SENTIMENT, SOURCE_NAME, CATEGORIES, KEYWORDS, BODY
```

#### Search Terms Used:
- bitcoin (10 articles)
- ethereum (10 articles)

#### Key Insights:
- **20 articles with full text** for detailed analysis
- Body text limited to 5,000 characters for CSV compatibility
- Includes complete article content for text analysis
- Perfect for sentiment analysis, NLP, or keyword extraction

---

## ❌ Failed Exports

### 5. **Feed Categories** (`feed_categories_*.csv`)
- **Status**: ❌ Failed
- **Error**: 404 Client Error - Endpoint not found
- **Reason**: The `/feed_category/list` endpoint doesn't exist in the API

**Workaround**: We can create our own source-category mapping from the existing sources and categories data.

---

## 📈 Data Statistics Summary

| Metric | Value |
|--------|-------|
| Total Sources | 44 |
| Total Categories | 182 |
| Total Articles (summary) | 500 |
| Total Articles (full body) | 20 |
| Avg Article Body Length | ~3,000 chars |
| Languages Supported | EN only |
| Sentiment Types | POSITIVE, NEUTRAL, NEGATIVE |

---

## 🔍 Data Structure Analysis

### Sources Structure
```json
{
  "ID": 5,
  "SOURCE_KEY": "coindesk",
  "NAME": "CoinDesk",
  "IMAGE_URL": "https://...",
  "URL": "https://www.coindesk.com/...",
  "LANG": "EN",
  "SOURCE_TYPE": "RSS",
  "LAUNCH_DATE": 1367884800,
  "SORT_ORDER": 0,
  "BENCHMARK_SCORE": 71,
  "STATUS": "ACTIVE"
}
```

**Key Fields**:
- `SOURCE_KEY`: Unique identifier (use in API calls)
- `BENCHMARK_SCORE`: Quality/reliability score (0-100)
- `STATUS`: ACTIVE/INACTIVE flag

---

### Categories Structure
```json
{
  "ID": 1,
  "NAME": "1INCH",
  "STATUS": "ACTIVE",
  "INCLUDED_WORDS": ["1INCH"],
  "INCLUDED_PHRASES": ["1INCH NETWORK"]
}
```

**Key Fields**:
- `ID`: Unique category identifier
- `INCLUDED_WORDS`: Keywords for auto-tagging
- `INCLUDED_PHRASES`: Multi-word phrases for matching

---

### Articles Structure
```json
{
  "ID": 52511128,
  "GUID": "fd38a385-aebf-4156-b6f4-a289cddcbef9",
  "TITLE": "Article Title",
  "SUBTITLE": "Article Subtitle",
  "AUTHORS": "Author Names",
  "PUBLISHED_ON": 1727712000,
  "URL": "https://...",
  "SENTIMENT": "POSITIVE",
  "SOURCE_KEY": "coindesk",
  "CATEGORIES": "CRYPTOCURRENCY, BTC, MARKET",
  "BODY_LENGTH": 2847
}
```

**Key Fields**:
- `GUID`: Globally unique identifier
- `SENTIMENT`: Automated sentiment classification
- `CATEGORIES`: Comma-separated category list
- `PUBLISHED_ON`: Unix timestamp

---

## 📊 Analysis Recommendations

### For Manual Analysis

1. **Open in Excel/Google Sheets**:
   - Import CSVs with UTF-8 encoding
   - Use filters to explore data
   - Create pivot tables for insights

2. **Source Analysis**:
   - Compare BENCHMARK_SCORE across sources
   - Identify top-tier vs. lower-tier sources
   - Check LAUNCH_DATE to see source age

3. **Category Analysis**:
   - Count articles per category
   - Identify trending categories
   - Map category keywords to article content

4. **Article Analysis**:
   - Sentiment distribution (POSITIVE vs. NEGATIVE)
   - Author productivity (articles per author)
   - Publication patterns (time of day, day of week)
   - Category co-occurrence patterns

5. **Content Analysis**:
   - BODY_LENGTH distribution
   - Keyword frequency in KEYWORDS field
   - Category overlap (which categories appear together)

---

## 🔧 Database Schema Recommendations

Based on this CSV structure, here's the recommended PostgreSQL schema:

```sql
-- Sources table
CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    source_key VARCHAR(100) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    image_url TEXT,
    url TEXT,
    lang VARCHAR(10),
    source_type VARCHAR(50),
    launch_date BIGINT,
    sort_order INTEGER,
    benchmark_score INTEGER,
    status VARCHAR(20),
    last_updated_ts BIGINT,
    created_on BIGINT,
    updated_on BIGINT
);

-- Categories table
CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20),
    included_words TEXT[],
    included_phrases TEXT[],
    created_on BIGINT,
    updated_on BIGINT
);

-- Articles table
CREATE TABLE articles (
    id BIGINT PRIMARY KEY,
    guid VARCHAR(255) UNIQUE NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    authors TEXT,
    published_on BIGINT,
    url TEXT UNIQUE,
    image_url TEXT,
    keywords TEXT,
    lang VARCHAR(10),
    sentiment VARCHAR(20),
    upvotes INTEGER DEFAULT 0,
    downvotes INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    status VARCHAR(20),
    source_key VARCHAR(100) REFERENCES sources(source_key),
    body TEXT,
    body_length INTEGER,
    created_on BIGINT,
    updated_on BIGINT,
    fetched_at TIMESTAMP DEFAULT NOW()
);

-- Article-Category mapping (many-to-many)
CREATE TABLE article_categories (
    article_id BIGINT REFERENCES articles(id),
    category_id INTEGER REFERENCES categories(id),
    PRIMARY KEY (article_id, category_id)
);

-- Indexes for performance
CREATE INDEX idx_articles_published ON articles(published_on DESC);
CREATE INDEX idx_articles_sentiment ON articles(sentiment);
CREATE INDEX idx_articles_source ON articles(source_key);
CREATE INDEX idx_articles_fetched ON articles(fetched_at DESC);
```

---

## 🎯 Next Steps

1. **Import CSVs into Database**:
   ```bash
   # Use PostgreSQL COPY command or Python script
   python import_csv_to_db.py
   ```

2. **Data Quality Checks**:
   - Check for duplicate articles (same URL/GUID)
   - Validate sentiment values
   - Check for missing critical fields

3. **Analysis Queries**:
   ```sql
   -- Sentiment distribution
   SELECT sentiment, COUNT(*)
   FROM articles
   GROUP BY sentiment;

   -- Top categories
   SELECT category, COUNT(*)
   FROM article_categories
   GROUP BY category
   ORDER BY COUNT(*) DESC
   LIMIT 10;

   -- Articles per source
   SELECT source_key, COUNT(*)
   FROM articles
   GROUP BY source_key;
   ```

4. **Automated Insights**:
   - Build sentiment trend tracker
   - Create category heatmap
   - Generate daily summary reports

---

## 📂 File Locations

**CSV Exports**: `C:\cpio_db\NewsFetcher\data\csv_exports\`

**Files**:
- `sources_20251001_145721.csv` (8.1 KB)
- `categories_20251001_145721.csv` (15 KB)
- `articles_20251001_145721.csv` (309 KB)
- `articles_full_body_20251001_145721.csv` (63 KB)

---

**Generated**: October 1, 2025
**Total Data Exported**: ~400 KB across 4 CSV files
**Total Records**: 746 (44 sources + 182 categories + 500 articles + 20 detailed articles)