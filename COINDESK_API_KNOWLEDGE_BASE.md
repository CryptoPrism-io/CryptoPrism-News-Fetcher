# CoinDesk Legacy News API (v1) - Complete Knowledge Base

## Table of Contents
1. [Overview](#overview)
2. [API Endpoints](#api-endpoints)
3. [Unified Data Model](#unified-data-model)
4. [Configuration & Best Practices](#configuration--best-practices)
5. [Practical Examples](#practical-examples)
6. [Integration Opportunities](#integration-opportunities)
7. [Response Schemas](#response-schemas)

## Overview

The CoinDesk Legacy News API provides access to live and historical news articles metadata, sources, and categories. It is structured into **four key endpoints**, each serving a different purpose for news aggregation and filtering.

### Core Capabilities
- Access to live and historical news articles
- Metadata retrieval for articles, sources, and categories
- Filtering and pagination support
- Time-based queries for incremental updates

## API Endpoints

### (A) Latest News Articles
**Endpoint:** `news_v1_article_list`

**Purpose:** Fetches a list of articles with metadata.

#### Key Parameters
| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `limit` | int | Number of results to return | 10-20 |
| `offset` | int | For pagination | - |
| `from` | timestamp | Fetch articles from this datetime onward | - |
| `to` | timestamp | Fetch articles up to this datetime | - |
| `category` | string | Filter by category (see category endpoint) | - |
| `source` | string | Filter by specific source(s) | - |

#### Response Schema
```json
{
  "articles": [
    {
      "id": "12345",
      "title": "Sample Headline",
      "summary": "Short snippet",
      "url": "https://www.coindesk.com/article-link",
      "image_url": "https://cdn.coindesk.com/image.jpg",
      "published_at": "2025-09-29T16:00:00Z",
      "source": "CoinDesk",
      "category": "Markets"
    }
  ],
  "count": 20,
  "offset": 0,
  "total": 1000
}
```

#### Use Cases
- Build infinite scroll news feeds (using `offset`)
- Incrementally fetch new news (using `from`)
- Filter dashboards by category or source

---

### (B) List News Feeds
**Endpoint:** `news_v1_source_list`

**Purpose:** Returns all available news sources.

#### Response Schema
```json
{
  "sources": [
    {"id": "coindesk", "name": "CoinDesk"},
    {"id": "reuters", "name": "Reuters"}
  ]
}
```

#### Use Cases
- Populate source filter dropdowns in UI
- Attribute news articles to their publishers
- Filter articles by trusted sources only

---

### (C) News Article Categories
**Endpoint:** `news_v1_category_list`

**Purpose:** Returns all available article categories.

#### Response Schema
```json
{
  "categories": [
    {"id": "markets", "name": "Markets"},
    {"id": "policy", "name": "Policy"},
    {"id": "defi", "name": "DeFi"}
  ]
}
```

#### Use Cases
- Populate filters or tabs in dashboards (Markets, Regulation, DeFi)
- Enable user personalization (choose preferred categories)

---

### (D) List News Feeds and Categories
**Endpoint:** `news_v1_feed_category_list`

**Purpose:** Returns combined mapping of sources and their categories.

#### Response Schema
```json
{
  "feeds": [
    {
      "source": "coindesk",
      "categories": ["markets", "policy"]
    },
    {
      "source": "reuters",
      "categories": ["global", "crypto"]
    }
  ]
}
```

#### Use Cases
- Build structured filters (e.g., show only Reuters + Markets)
- Design hierarchical dropdowns (Source → Categories)
- Configure alert systems based on specific source-category pairs

## Unified Data Model

### Recommended Article Object Schema
```json
{
  "id": "string",              // Unique identifier
  "title": "string",           // Headline
  "summary": "string",         // Snippet / excerpt
  "url": "string",             // Article link
  "image_url": "string",       // Article image link
  "published_at": "timestamp", // Publication timestamp
  "source": "string",          // Source ID
  "source_name": "string",     // Source name (via source list)
  "category": "string",        // Category ID
  "category_name": "string"    // Category name (via category list)
}
```

## Configuration & Best Practices

### 🔄 Incremental Fetching
- **Always use `from`** to minimize redundancy
- Store last fetch timestamp and use it for subsequent requests
- Reduces API calls and improves performance

### 📄 Pagination
- **Use `offset`** to implement infinite scroll
- Standard pagination pattern for large result sets
- Combine with `limit` for controlled data loading

### 🔍 Filters
- **Integrate both sources and categories** for personalization
- Allow users to customize their news feed
- Enable complex filtering combinations

### 💾 Caching
- **Cache source and category lists** (rarely change)
- Implement client-side caching for metadata
- Reduces API load for static data

### ⚠️ Error Handling
- **Implement retries and backoff** for rate limits
- Handle network failures gracefully
- Log errors for monitoring and debugging

### 🚫 Deduplication
- **Use `id` or `url`** to prevent duplicates
- Implement client-side duplicate detection
- Maintain data integrity across fetches

## Practical Examples

### Example 1: Fetch Latest 20 Articles
```http
GET /data/news/v1/article/list?limit=20
```

### Example 2: Fetch Articles Since Last Fetch Time
```http
GET /data/news/v1/article/list?from=2025-09-29T00:00:00Z
```

### Example 3: Get Sources for Dropdown
```http
GET /data/news/v1/source/list
```

### Example 4: Get Categories for UI Filters
```http
GET /data/news/v1/category/list
```

### Example 5: Get Feeds & Categories Together
```http
GET /data/news/v1/feed_category/list
```

### Example 6: Filter by Category and Source
```http
GET /data/news/v1/article/list?category=markets&source=coindesk&limit=10
```

### Example 7: Paginated Results
```http
GET /data/news/v1/article/list?limit=20&offset=20
```

### Example 8: Time Range Query
```http
GET /data/news/v1/article/list?from=2025-09-29T00:00:00Z&to=2025-09-29T23:59:59Z
```

## Integration Opportunities

### 📊 Dashboards
- Build category-filtered news widgets
- Create real-time news monitoring interfaces
- Implement customizable news feeds

### 🔔 Notifications
- Push alerts for breaking news by category
- Set up source-specific notifications
- Configure keyword-based alerts

### 📈 Analytics
- Track volume of news by category over time
- Monitor trending topics and sources
- Generate news sentiment analysis

### 🤖 Social Automation
- Auto-post trending articles from selected sources
- Schedule social media content based on news
- Integrate with content management systems

## Response Schemas

### Complete API Response Examples

#### Article List Response
```json
{
  "articles": [
    {
      "id": "12345",
      "title": "Bitcoin Reaches New All-Time High",
      "summary": "Bitcoin surged past $70,000 as institutional adoption continues...",
      "url": "https://www.coindesk.com/markets/2025/09/29/bitcoin-reaches-new-ath",
      "image_url": "https://cdn.coindesk.com/wp-content/uploads/2025/09/bitcoin-chart.jpg",
      "published_at": "2025-09-29T16:00:00Z",
      "source": "CoinDesk",
      "category": "Markets"
    }
  ],
  "count": 20,
  "offset": 0,
  "total": 1000
}
```

#### Sources Response
```json
{
  "sources": [
    {"id": "coindesk", "name": "CoinDesk"},
    {"id": "reuters", "name": "Reuters"},
    {"id": "bloomberg", "name": "Bloomberg"},
    {"id": "cointelegraph", "name": "Cointelegraph"}
  ]
}
```

#### Categories Response
```json
{
  "categories": [
    {"id": "markets", "name": "Markets"},
    {"id": "policy", "name": "Policy"},
    {"id": "defi", "name": "DeFi"},
    {"id": "nft", "name": "NFT"},
    {"id": "tech", "name": "Technology"}
  ]
}
```

#### Feed Categories Response
```json
{
  "feeds": [
    {
      "source": "coindesk",
      "categories": ["markets", "policy", "defi", "tech"]
    },
    {
      "source": "reuters",
      "categories": ["global", "crypto", "markets"]
    },
    {
      "source": "bloomberg",
      "categories": ["markets", "finance", "crypto"]
    }
  ]
}
```

## Implementation Notes

### Rate Limiting
- Implement exponential backoff for failed requests
- Monitor rate limit headers if available
- Cache frequently accessed data to reduce API calls

### Data Validation
- Validate timestamp formats before sending requests
- Sanitize user input for category and source filters
- Handle malformed responses gracefully

### Performance Optimization
- Use connection pooling for HTTP requests
- Implement request batching where possible
- Consider using webhooks for real-time updates if available

### Security Considerations
- Store API keys securely
- Use HTTPS for all API communications
- Implement proper authentication mechanisms

---

*This knowledge base covers all endpoints, parameters, response schemas, and recommended usage patterns for the CoinDesk Legacy News API (v1).*