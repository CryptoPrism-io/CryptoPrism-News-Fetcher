import os
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_PORT = os.getenv("DB_PORT", "5432")


def get_db_connection():
    """Create and return a database connection"""
    return psycopg2.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME,
        port=DB_PORT
    )


def create_cc_news_table():
    """
    Create the cc_news table if it doesn't exist.

    Table schema designed to store CryptoCompare news articles with:
    - Unique article ID as primary key
    - Full article metadata and body text
    - Timestamps for tracking
    - Indexes for common queries
    """

    create_table_sql = """
    CREATE TABLE IF NOT EXISTS cc_news (
        id BIGINT PRIMARY KEY,
        title TEXT NOT NULL,
        published_on TIMESTAMP WITH TIME ZONE NOT NULL,
        source VARCHAR(100),
        source_name VARCHAR(255),
        url TEXT UNIQUE NOT NULL,
        categories TEXT,
        tags TEXT,
        lang VARCHAR(10),
        body TEXT,
        body_length INTEGER,
        has_image BOOLEAN,
        imageurl TEXT,
        upvotes INTEGER DEFAULT 0,
        downvotes INTEGER DEFAULT 0,
        fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );

    -- Create indexes for common queries
    CREATE INDEX IF NOT EXISTS idx_cc_news_published_on ON cc_news(published_on DESC);
    CREATE INDEX IF NOT EXISTS idx_cc_news_source ON cc_news(source);
    CREATE INDEX IF NOT EXISTS idx_cc_news_categories ON cc_news USING gin(string_to_array(categories, '|'));
    CREATE INDEX IF NOT EXISTS idx_cc_news_fetched_at ON cc_news(fetched_at DESC);

    -- Add comment to table
    COMMENT ON TABLE cc_news IS 'CryptoCompare cryptocurrency news articles - fetched hourly';
    """

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(create_table_sql)
        conn.commit()
        print("✅ Table 'cc_news' created successfully (or already exists)")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating table: {e}")
        raise
    finally:
        cur.close()
        conn.close()


def insert_articles(articles: list) -> dict:
    """
    Insert articles into cc_news table.
    Uses ON CONFLICT DO NOTHING to avoid duplicates (based on unique URL).

    Args:
        articles: List of article dictionaries from CryptoCompare API

    Returns:
        dict: Statistics about the insertion (inserted, duplicates, errors)
    """

    if not articles:
        print("⚠️  No articles to insert")
        return {"inserted": 0, "duplicates": 0, "errors": 0}

    # Prepare data for insertion
    rows = []
    for article in articles:
        # Convert timestamp to datetime
        published_on = datetime.fromtimestamp(article.get("published_on", 0))

        rows.append((
            int(article.get("id", 0)),
            article.get("title", ""),
            published_on,
            article.get("source", ""),
            article.get("source_info", {}).get("name", ""),
            article.get("url", ""),
            article.get("categories", ""),
            article.get("tags", ""),
            article.get("lang", "EN"),
            article.get("body", ""),
            len(article.get("body", "")),
            bool(article.get("imageurl")),
            article.get("imageurl", ""),
            int(article.get("upvotes", 0)),
            int(article.get("downvotes", 0)),
            datetime.now()  # fetched_at
        ))

    insert_sql = """
    INSERT INTO cc_news (
        id, title, published_on, source, source_name, url,
        categories, tags, lang, body, body_length,
        has_image, imageurl, upvotes, downvotes, fetched_at
    ) VALUES %s
    ON CONFLICT (url) DO NOTHING
    """

    conn = get_db_connection()
    cur = conn.cursor()

    stats = {"inserted": 0, "duplicates": 0, "errors": 0}

    try:
        # Get count before insertion
        cur.execute("SELECT COUNT(*) FROM cc_news")
        count_before = cur.fetchone()[0]

        # Execute batch insert
        execute_values(cur, insert_sql, rows)
        conn.commit()

        # Get count after insertion
        cur.execute("SELECT COUNT(*) FROM cc_news")
        count_after = cur.fetchone()[0]

        stats["inserted"] = count_after - count_before
        stats["duplicates"] = len(articles) - stats["inserted"]

        print(f"✅ Database insertion complete:")
        print(f"   - New articles inserted: {stats['inserted']}")
        print(f"   - Duplicates skipped: {stats['duplicates']}")
        print(f"   - Total articles in DB: {count_after}")

    except Exception as e:
        conn.rollback()
        stats["errors"] = len(articles)
        print(f"❌ Error inserting articles: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    return stats


def get_latest_articles(limit=10):
    """Get the latest articles from the database"""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id, title, published_on, source_name, url
            FROM cc_news
            ORDER BY published_on DESC
            LIMIT %s
        """, (limit,))

        articles = cur.fetchall()
        return articles
    finally:
        cur.close()
        conn.close()


def get_table_stats():
    """Get statistics about the cc_news table"""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        stats = {}

        # Total count
        cur.execute("SELECT COUNT(*) FROM cc_news")
        stats["total_articles"] = cur.fetchone()[0]

        # Date range
        cur.execute("""
            SELECT
                MIN(published_on) as oldest,
                MAX(published_on) as newest
            FROM cc_news
        """)
        oldest, newest = cur.fetchone()
        stats["oldest_article"] = oldest
        stats["newest_article"] = newest

        # Unique sources
        cur.execute("SELECT COUNT(DISTINCT source) FROM cc_news")
        stats["unique_sources"] = cur.fetchone()[0]

        # Articles fetched today
        cur.execute("""
            SELECT COUNT(*) FROM cc_news
            WHERE fetched_at >= CURRENT_DATE
        """)
        stats["articles_today"] = cur.fetchone()[0]

        return stats
    finally:
        cur.close()
        conn.close()


def test_connection():
    """Test database connection"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT version();")
        version = cur.fetchone()[0]
        cur.close()
        conn.close()
        print(f"✅ Database connection successful!")
        print(f"   PostgreSQL version: {version}")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False


if __name__ == "__main__":
    print("🔧 Testing database connection and table setup...\n")

    # Test connection
    if test_connection():
        print()

        # Create table
        create_cc_news_table()
        print()

        # Show stats if table has data
        try:
            stats = get_table_stats()
            print("📊 Current table statistics:")
            print(f"   Total articles: {stats['total_articles']}")
            print(f"   Unique sources: {stats['unique_sources']}")
            print(f"   Articles fetched today: {stats['articles_today']}")
            if stats['oldest_article']:
                print(f"   Date range: {stats['oldest_article']} to {stats['newest_article']}")
        except Exception as e:
            print(f"⚠️  Could not fetch stats (table may be empty): {e}")
