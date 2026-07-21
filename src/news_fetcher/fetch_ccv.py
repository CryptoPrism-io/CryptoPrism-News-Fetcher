"""Ingester for the self-hosted cryptocurrency.cv news aggregator.

Pipeline:  /api/news (list)  ->  /api/news/extract (full body)  ->  cc_news schema

Why two calls: the cv feed only returns ~200-char descriptions (no body), which
fall under sentiment.py's MIN_BODY_LENGTH=300 filter. The POST /api/news/extract
endpoint fetches each article's source page and returns clean full text, which is
what the sentiment / event NLP actually needs.

Access notes (self-hosted):
  * No API key required.
  * Send header `Sec-Fetch-Site: same-origin` — this marks the request as a
    trusted same-origin browser fetch, bypassing the 3-article free-tier cap
    (see src/middleware/trusted-origins.ts in the cv repo).
  * The unfiltered /api/news fan-out over ~200 feeds is flaky on a cold/in-memory
    cache; source/category-filtered queries are reliable, so we aggregate those
    as a fallback.

Run modes:
  python -m src.news_fetcher.fetch_ccv --json out.json            # local dump
  python -m src.news_fetcher.fetch_ccv --db --table cc_news_cv_eval  # insert to DB
"""
import os
import json
import time
import hashlib
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timezone

CCV_BASE = os.getenv("CCV_BASE_URL", "http://localhost:3000")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Sec-Fetch-Site": "same-origin"}

# Aggregated when the unfiltered feed returns empty (cold-cache resilience).
FALLBACK_QUERIES = [
    "category=bitcoin", "category=ethereum", "category=defi",
    "category=altcoin", "category=regulation", "category=markets",
    "source=cointelegraph", "source=decrypt", "source=theblock",
    "source=beincrypto", "source=cryptoslate", "source=bitcoinmagazine",
]


def _get(path, timeout=90):
    req = urllib.request.Request(CCV_BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post(path, payload, timeout=45):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        CCV_BASE + path, data=data,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_articles(limit=100):
    """Return a de-duplicated list of feed articles (metadata only, no body)."""
    seen, out = set(), []

    def take(d):
        for a in d.get("articles", []):
            link = a.get("link")
            if link and link not in seen:
                seen.add(link)
                out.append(a)

    # 1) try the unfiltered feed
    try:
        take(_get(f"/api/news?limit={limit}"))
    except Exception as e:
        print(f"   unfiltered feed error: {e}")

    # 2) if thin, aggregate across filtered queries
    if len(out) < limit:
        for q in FALLBACK_QUERIES:
            if len(out) >= limit:
                break
            try:
                take(_get(f"/api/news?{q}&limit=40"))
            except Exception:
                continue
            time.sleep(0.1)

    return out[:limit]


def extract_body(url):
    """Full article body via POST /api/news/extract; '' on failure."""
    try:
        d = _post("/api/news/extract", {"url": url})
        return d.get("content") or d.get("text") or ""
    except Exception:
        return ""


def link_to_id(link):
    """Stable signed 64-bit id from the article URL (cc_news.id is BIGINT)."""
    h = hashlib.sha1(link.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=True)


def normalize(a, body):
    """Map a cv article + extracted body to the cc_news row shape."""
    return {
        "id": link_to_id(a.get("link", "")),
        "title": a.get("title", ""),
        "published_on": a.get("pubDate", ""),
        "source": a.get("sourceKey") or a.get("source", ""),
        "source_name": a.get("source", ""),
        "url": a.get("link", ""),
        "categories": a.get("category", ""),
        "tags": "",
        "lang": a.get("lang", "EN"),
        "body": body,
        "body_length": len(body),
        "has_image": bool(a.get("image") or a.get("imageurl")),
        "imageurl": a.get("image", "") or "",
        "upvotes": 0,
        "downvotes": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def ingest(limit=60, with_body=True):
    arts = fetch_articles(limit)
    print(f"   feed: {len(arts)} unique articles")
    rows, ok = [], 0
    for i, a in enumerate(arts, 1):
        body = extract_body(a["link"]) if with_body else ""
        if body:
            ok += 1
        rows.append(normalize(a, body))
        if i % 10 == 0:
            print(f"   extracted {i}/{len(arts)} (bodies ok: {ok})")
    print(f"   extraction: {ok}/{len(rows)} articles got a body")
    return rows


def metrics(rows, label):
    n = len(rows)
    bl = [r["body_length"] for r in rows]
    over = sum(1 for x in bl if x >= 300)
    srcs = {r["source_name"] for r in rows}
    dates = sorted(r["published_on"][:10] for r in rows if r["published_on"])
    med = sorted(bl)[n // 2] if n else 0
    print(f"\n--- {label} ---")
    print(f"  articles          : {n}")
    print(f"  distinct sources  : {len(srcs)}")
    print(f"  body_length>=300  : {over} ({100*over//n if n else 0}%)")
    print(f"  body_length med/max: {med}/{max(bl) if bl else 0}")
    print(f"  date range        : {dates[0] if dates else '-'} .. {dates[-1] if dates else '-'}")
    return {"articles": n, "sources": len(srcs), "body_over_300_pct": (100*over//n if n else 0),
            "body_median": med, "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None}


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    id BIGINT PRIMARY KEY,
    title TEXT NOT NULL,
    published_on TIMESTAMPTZ NOT NULL,
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
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def insert_db(rows, table):
    """Insert into an isolated eval table (NOT prod cc_news). Zero-touch safe."""
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        dbname=os.environ["DB_NAME"],
    )
    cur = conn.cursor()
    cur.execute(TABLE_DDL.format(table=table))
    conn.commit()
    vals = [(
        r["id"], r["title"], r["published_on"], r["source"], r["source_name"],
        r["url"], r["categories"], r["tags"], r["lang"], r["body"],
        r["body_length"], r["has_image"], r["imageurl"], r["upvotes"],
        r["downvotes"], r["fetched_at"],
    ) for r in rows]
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    before = cur.fetchone()[0]
    execute_values(cur, f"""
        INSERT INTO {table} (id,title,published_on,source,source_name,url,categories,
            tags,lang,body,body_length,has_image,imageurl,upvotes,downvotes,fetched_at)
        VALUES %s ON CONFLICT (url) DO NOTHING
    """, vals)
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    after = cur.fetchone()[0]
    cur.close(); conn.close()
    print(f"   DB: inserted {after-before} new rows into {table} (total {after})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--no-body", action="store_true")
    ap.add_argument("--json", metavar="PATH", help="dump normalized rows to JSON")
    ap.add_argument("--db", action="store_true", help="insert into --table")
    ap.add_argument("--table", default="cc_news_cv_eval")
    args = ap.parse_args()

    print(f"Ingesting from {CCV_BASE} (limit={args.limit}, body={not args.no_body})...")
    rows = ingest(limit=args.limit, with_body=not args.no_body)
    metrics(rows, f"cryptocurrency.cv ({args.table})")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"   wrote {len(rows)} rows -> {args.json}")
    if args.db:
        insert_db(rows, args.table)
