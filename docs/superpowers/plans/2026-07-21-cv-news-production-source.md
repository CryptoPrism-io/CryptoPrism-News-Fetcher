# cryptocurrency.cv Production News Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run self-hosted cryptocurrency.cv (with Redis) on an AWS EC2 VM as a reliable production news source that feeds `cc_news` hourly with full article bodies, alongside the existing CoinDesk fetch.

**Architecture:** cv + Redis run as Docker containers on an EC2 t3.small (cv bound to localhost). A host systemd timer runs the Python ingester hourly: it polls cv's `/api/news`, extracts full bodies via `/api/news/extract`, and upserts into `cc_news` deduped by URL. Deployment is via a GitHub Actions SSH workflow (fleet `neelkanth_c3` pattern).

**Tech Stack:** Python 3.11 (psycopg2), Docker Compose, Redis 7, systemd, Next.js (cv, prebuilt), GitHub Actions SSH deploy.

## Global Constraints

- Zero-touch: only READ/upsert `cc_news`; never ALTER/DROP it. New tables/files only.
- Dedup into `cc_news` by `url` (`ON CONFLICT (url) DO NOTHING`). Never run CREATE/ALTER DDL against `cc_news`.
- cv full access requires header `Sec-Fetch-Site: same-origin`.
- cv bound to `127.0.0.1:3000` — never publicly exposed.
- DB credentials come from env (`DB_HOST/PORT/USER/PASSWORD/NAME`); source of truth is the GitHub Actions secrets, NOT local `.env` (which is stale post-migration).
- Ingester must fail LOUD (non-zero exit) on cv-unreachable, DB-unreachable, or 0-articles-from-warm-cache. No silent green.
- All new Python targets Python 3.11 (the cv VM + Actions runner).

---

## Task 1: P0 — Verify current DB host/type post-migration

**Why first:** The AWS migration likely changed the DB host; the firewall/allowlist mechanism (AWS RDS security group vs GCP firewall) depends on the answer. Local `.env` says GCP `34.55.195.199` but the working value is the Actions `DB_HOST` secret.

**Files:**
- Create: `scripts/db_identity_check.py`
- Create: `.github/workflows/db-identity-check.yml`

**Interfaces:**
- Produces: a documented fact — current DB server address, version, RDS-vs-GCE — recorded in the runbook (Task 6).

- [ ] **Step 1: Write the identity-check script**

```python
# scripts/db_identity_check.py
"""Print the current Postgres server identity so we know where it lives
post-AWS-migration (RDS vs GCE) and what IP to allowlist."""
import os
import psycopg2

conn = psycopg2.connect(
    host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
    user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    dbname=os.environ["DB_NAME"],
)
cur = conn.cursor()
cur.execute("SELECT current_database(), inet_server_addr(), inet_server_port(), version();")
db, addr, port, ver = cur.fetchone()
print(f"database        : {db}")
print(f"server address  : {addr}")   # 10.x/172.x = private (likely RDS/VPC); public = exposed
print(f"server port     : {port}")
print(f"version         : {ver}")
# RDS builds usually mention 'Amazon' or a distinct build; GCE self-managed does not.
print(f"looks_like_rds  : {'amazon' in ver.lower() or 'rds' in ver.lower()}")
cur.execute("SELECT count(*) FROM cc_news;")
print(f"cc_news rows    : {cur.fetchone()[0]}")
cur.close(); conn.close()
```

- [ ] **Step 2: Write the workflow**

```yaml
# .github/workflows/db-identity-check.yml
name: DB Identity Check (read-only)
on:
  workflow_dispatch:
jobs:
  identify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install "psycopg2-binary>=2.9"
      - name: Identify DB
        env:
          DB_HOST: ${{ secrets.DB_HOST }}
          DB_PORT: ${{ secrets.DB_PORT }}
          DB_USER: ${{ secrets.DB_USER }}
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
          DB_NAME: ${{ secrets.DB_NAME }}
        run: python scripts/db_identity_check.py
```

- [ ] **Step 3: Commit, push, run**

```bash
git add scripts/db_identity_check.py .github/workflows/db-identity-check.yml
git commit -m "feat(news): P0 DB identity check workflow"
git push
gh workflow run db-identity-check.yml
```

- [ ] **Step 4: Record the result**

Run: `gh run list --workflow=db-identity-check.yml --limit 1` then `gh run view <id> --log`
Expected: prints server address + version. **Record `server address`, `looks_like_rds` in the runbook (Task 6).** This decides the allowlist mechanism.

---

## Task 2: Ingester production mode (`fetch_ccv.py`)

**Files:**
- Modify: `src/news_fetcher/fetch_ccv.py` (extend existing module)
- Test: `tests/test_fetch_ccv.py`

**Interfaces:**
- Consumes: existing `fetch_articles(limit)`, `extract_body(url)`, `link_to_id(link)`, `normalize(a, body)` from `fetch_ccv.py`.
- Produces:
  - `get_existing_urls(conn, urls) -> set[str]`
  - `insert_db(rows, table, create_table=True)` (add `create_table` param; default True; MUST be False for `cc_news`)
  - `run_production(limit=200, table="cc_news") -> dict` (orchestrator, loud-fail)
  - CLI flag `--prod` invoking `run_production`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fetch_ccv.py
import importlib
mod = importlib.import_module("src.news_fetcher.fetch_ccv")


def test_link_to_id_is_stable_and_signed64():
    a = mod.link_to_id("https://x.com/a")
    b = mod.link_to_id("https://x.com/a")
    c = mod.link_to_id("https://x.com/b")
    assert a == b and a != c
    assert -(2**63) <= a < 2**63


def test_normalize_maps_cv_to_ccnews_shape():
    art = {"link": "https://x.com/a", "title": "T", "pubDate": "2026-07-21T00:00:00Z",
           "sourceKey": "decrypt", "source": "Decrypt", "category": "bitcoin",
           "image": "https://img/x.png"}
    row = mod.normalize(art, "body text " * 40)
    assert row["url"] == "https://x.com/a"
    assert row["source"] == "decrypt" and row["source_name"] == "Decrypt"
    assert row["categories"] == "bitcoin"
    assert row["body_length"] == len("body text " * 40)
    assert row["has_image"] is True
    assert row["id"] == mod.link_to_id("https://x.com/a")


def test_get_existing_urls_filters(monkeypatch):
    class FakeCur:
        def execute(self, sql, params=None): self._rows = [("https://x.com/a",)]
        def fetchall(self): return self._rows
        def close(self): pass
    class FakeConn:
        def cursor(self): return FakeCur()
    seen = mod.get_existing_urls(FakeConn(), ["https://x.com/a", "https://x.com/b"])
    assert seen == {"https://x.com/a"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetch_ccv.py -v`
Expected: FAIL — `get_existing_urls` not defined.

- [ ] **Step 3: Add `get_existing_urls` and extend `insert_db`**

```python
# add to src/news_fetcher/fetch_ccv.py

def get_existing_urls(conn, urls):
    """Return the subset of urls already present in the target table."""
    if not urls:
        return set()
    cur = conn.cursor()
    cur.execute(
        "SELECT url FROM cc_news WHERE url = ANY(%s)", (list(urls),))
    seen = {r[0] for r in cur.fetchall()}
    cur.close()
    return seen
```

Modify `insert_db` signature and DDL guard:

```python
def insert_db(rows, table, create_table=True, conn=None):
    """Insert rows. For prod cc_news pass create_table=False (never DDL prod)."""
    import psycopg2
    from psycopg2.extras import execute_values
    own = conn is None
    if own:
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
            user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
            dbname=os.environ["DB_NAME"])
    cur = conn.cursor()
    if create_table:
        cur.execute(TABLE_DDL.format(table=table)); conn.commit()
    vals = [(
        r["id"], r["title"], r["published_on"], r["source"], r["source_name"],
        r["url"], r["categories"], r["tags"], r["lang"], r["body"],
        r["body_length"], r["has_image"], r["imageurl"], r["upvotes"],
        r["downvotes"], r["fetched_at"]) for r in rows]
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    before = cur.fetchone()[0]
    execute_values(cur, f"""
        INSERT INTO {table} (id,title,published_on,source,source_name,url,categories,
            tags,lang,body,body_length,has_image,imageurl,upvotes,downvotes,fetched_at)
        VALUES %s ON CONFLICT (url) DO NOTHING""", vals)
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    after = cur.fetchone()[0]
    cur.close()
    if own:
        conn.close()
    inserted = after - before
    print(f"   DB: inserted {inserted} new rows into {table} (total {after})")
    return inserted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetch_ccv.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Add `run_production` orchestrator + loud failure + CLI**

```python
# add to src/news_fetcher/fetch_ccv.py
import sys


def _notify_failure(msg):
    """Best-effort Telegram ping; never raises."""
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    try:
        payload = json.dumps({"chat_id": chat, "text": f"⚠️ cv-ingester: {msg}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def run_production(limit=200, table="cc_news"):
    """Fetch → dedup vs table → extract new → upsert. Exit non-zero on hard failure."""
    import psycopg2
    # 1. DB up?
    try:
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
            user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
            dbname=os.environ["DB_NAME"])
    except Exception as e:
        _notify_failure(f"DB unreachable: {e}"); print(f"FATAL DB: {e}"); sys.exit(1)

    # 2. cv feed (warm cache expected in prod via Redis)
    arts = fetch_articles(limit)
    if not arts:
        _notify_failure("cv returned 0 articles (warm cache) — feed broken?")
        print("FATAL: 0 articles from cv"); conn.close(); sys.exit(2)

    # 3. dedup vs existing
    by_url = {a["link"]: a for a in arts if a.get("link")}
    existing = get_existing_urls(conn, list(by_url))
    new_arts = [a for u, a in by_url.items() if u not in existing]
    print(f"   feed={len(arts)} new={len(new_arts)} (skipped {len(existing)} known)")

    # 4. extract bodies for new only
    rows, ok = [], 0
    for a in new_arts:
        body = extract_body(a["link"])
        if body:
            ok += 1
        rows.append(normalize(a, body))

    # 5. upsert (never DDL prod cc_news)
    inserted = insert_db(rows, table, create_table=False, conn=conn) if rows else 0
    over = sum(1 for r in rows if r["body_length"] >= 300)
    print(f"   inserted={inserted} bodies_ok={ok}/{len(rows)} body>=300={over}")
    conn.close()
    return {"feed": len(arts), "new": len(new_arts), "inserted": inserted,
            "bodies_ok": ok, "body_over_300": over}
```

Add `--prod` to the CLI `__main__` block:

```python
    ap.add_argument("--prod", action="store_true",
                    help="production run: dedup vs cc_news + loud failure")
    # ... after parse_args(), before the existing --json/--db logic:
    if args.prod:
        run_production(limit=args.limit, table=args.table)
        raise SystemExit(0)
```

- [ ] **Step 6: Run full test suite + a lint import check**

Run: `python -m pytest tests/test_fetch_ccv.py -v && python -c "import ast; ast.parse(open('src/news_fetcher/fetch_ccv.py').read()); print('syntax ok')"`
Expected: tests PASS, "syntax ok".

- [ ] **Step 7: Commit**

```bash
git add src/news_fetcher/fetch_ccv.py tests/test_fetch_ccv.py
git commit -m "feat(news): cv ingester production mode (dedup, loud-fail, telegram)"
```

---

## Task 3: cv + Redis docker-compose

**Files:**
- Create: `deploy/cv/docker-compose.yml`
- Create: `deploy/cv/.env.example`
- Create: `deploy/cv/README.md`

**Interfaces:**
- Produces: cv reachable at `http://127.0.0.1:3000` with Redis-backed cache.

- [ ] **Step 1: Write docker-compose**

```yaml
# deploy/cv/docker-compose.yml
services:
  cv:
    build:
      context: https://github.com/nirholas/cryptocurrency.cv.git#main
    image: cryptocurrency-cv:local
    restart: unless-stopped
    ports:
      - "127.0.0.1:3000:3000"   # localhost only — never public
    environment:
      REDIS_URL: "redis://redis:6379"
      NODE_ENV: "production"
      # GROQ_API_KEY intentionally empty — we do not use cv AI features
    depends_on:
      - redis
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--save", "60", "1", "--appendonly", "no"]
    volumes:
      - cv_redis:/data
volumes:
  cv_redis:
```

- [ ] **Step 2: Write env template + README**

```bash
# deploy/cv/.env.example
# cv self-host — copy to .env on the VM. All optional; core news needs none.
GROQ_API_KEY=
```

```markdown
<!-- deploy/cv/README.md -->
# cv self-host (cv + Redis)

On the EC2 VM:
    cd deploy/cv
    docker compose up -d --build
    curl -s -H "Sec-Fetch-Site: same-origin" http://127.0.0.1:3000/api/news?limit=5

cv is bound to localhost only. Redis provides the warm cache that keeps the
200-feed aggregation from returning 0 articles.
```

- [ ] **Step 3: Validate compose syntax**

Run: `docker compose -f deploy/cv/docker-compose.yml config`
Expected: prints resolved config, exit 0. (If Docker daemon is off locally, this still parses the file; a non-zero here means a YAML/schema error to fix.)

- [ ] **Step 4: Commit**

```bash
git add deploy/cv/
git commit -m "feat(news): cv + redis docker-compose (localhost-bound)"
```

---

## Task 4: systemd unit + timer for the ingester

**Files:**
- Create: `deploy/systemd/cv-ingester.service`
- Create: `deploy/systemd/cv-ingester.timer`
- Create: `deploy/systemd/cv-ingester.env.example`

**Interfaces:**
- Consumes: `python -m src.news_fetcher.fetch_ccv --prod` from Task 2.
- Produces: hourly ingest runs on the VM with journald logs.

- [ ] **Step 1: Write the service unit**

```ini
# deploy/systemd/cv-ingester.service
[Unit]
Description=cryptocurrency.cv -> cc_news ingester
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/cpio-news-fetcher
EnvironmentFile=/etc/cv-ingester.env
ExecStart=/usr/bin/python3 -m src.news_fetcher.fetch_ccv --prod --limit 200 --table cc_news
# Loud failure surfaces in `systemctl status` / journald and (Task 2) Telegram.
```

- [ ] **Step 2: Write the timer**

```ini
# deploy/systemd/cv-ingester.timer
[Unit]
Description=Run cv-ingester hourly

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write the env template**

```bash
# deploy/systemd/cv-ingester.env.example  -> install as /etc/cv-ingester.env
CCV_BASE_URL=http://127.0.0.1:3000
DB_HOST=
DB_PORT=5432
DB_USER=
DB_PASSWORD=
DB_NAME=dbcp
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 4: Validate unit syntax**

Run: `python -c "import configparser; c=configparser.ConfigParser(strict=False); c.read('deploy/systemd/cv-ingester.service'); c.read('deploy/systemd/cv-ingester.timer'); print('units parse ok')"`
Expected: "units parse ok". (Full `systemd-analyze verify` happens on the VM in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add deploy/systemd/
git commit -m "feat(news): systemd service+timer for cv ingester"
```

---

## Task 5: GitHub Actions SSH deploy workflow

**Files:**
- Create: `.github/workflows/deploy-cv-vm.yml`

**Interfaces:**
- Consumes: repo contents + `deploy/` artifacts.
- Produces: on push to `main` (paths `deploy/**`, `src/news_fetcher/**`) or manual dispatch, SSHes to the VM, updates the checkout, rebuilds compose, restarts the timer.

- [ ] **Step 1: Write the deploy workflow (fleet neelkanth_c3 SSH pattern)**

```yaml
# .github/workflows/deploy-cv-vm.yml
name: Deploy cv news source (AWS VM)
on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - 'deploy/**'
      - 'src/news_fetcher/**'
      - '.github/workflows/deploy-cv-vm.yml'
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy over SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VM_SSH_HOST }}
          username: ${{ secrets.VM_SSH_USER }}
          key: ${{ secrets.VM_SSH_KEY }}
          script: |
            set -euo pipefail
            cd /opt/cpio-news-fetcher
            git fetch --all
            git reset --hard origin/main
            cd deploy/cv
            docker compose up -d --build
            # health gate — do not restart the timer if cv is not serving
            sleep 8
            curl -fsS -H "Sec-Fetch-Site: same-origin" \
              "http://127.0.0.1:3000/api/health" > /dev/null
            sudo cp /opt/cpio-news-fetcher/deploy/systemd/cv-ingester.service /etc/systemd/system/
            sudo cp /opt/cpio-news-fetcher/deploy/systemd/cv-ingester.timer /etc/systemd/system/
            sudo systemctl daemon-reload
            sudo systemctl enable --now cv-ingester.timer
            echo "deploy ok"
```

- [ ] **Step 2: Validate workflow YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-cv-vm.yml')); print('workflow yaml ok')"`
Expected: "workflow yaml ok".

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-cv-vm.yml
git commit -m "feat(news): SSH deploy workflow for cv VM"
```

---

## Task 6: AWS runbook (one-time bring-up)

**Files:**
- Create: `docs/runbooks/cv-aws-setup.md`

**Interfaces:**
- Consumes: the DB identity fact from Task 1; the artifacts from Tasks 2–5.
- Produces: a checklist the user executes with their AWS creds.

- [ ] **Step 1: Write the runbook**

````markdown
<!-- docs/runbooks/cv-aws-setup.md -->
# cv news source — AWS bring-up runbook

## 0. Prereqs (record from Task 1)
- DB server address: __________  |  looks_like_rds: __________

## 1. Launch EC2
- t3.small, Ubuntu 24.04, 20 GB gp3. Attach an **Elastic IP** (stable allowlist IP).
- Security group inbound: **SSH (22) from your IP only**. Outbound: 443 + 5432.

## 2. Allowlist the VM on Postgres  ← migration-risk step
- If **RDS**: add the VM (same VPC private IP, or Elastic IP) to the RDS security group inbound 5432.
- If **GCE**: add the Elastic IP to the GCP firewall inbound 5432.
- Verify from the VM BEFORE proceeding:
      psql "host=<DB_HOST> port=5432 user=<DB_USER> dbname=<DB_NAME>" -c "select 1"

## 3. Install Docker + clone
    curl -fsSL https://get.docker.com | sh
    sudo git clone https://github.com/CryptoPrism-io/CryptoPrism-News-Fetcher /opt/cpio-news-fetcher

## 4. Configure env
    sudo cp /opt/cpio-news-fetcher/deploy/systemd/cv-ingester.env.example /etc/cv-ingester.env
    sudo nano /etc/cv-ingester.env   # fill DB_* (from GitHub secrets) + TELEGRAM_*

## 5. Bring up cv + Redis
    cd /opt/cpio-news-fetcher/deploy/cv && sudo docker compose up -d --build
    curl -s -H "Sec-Fetch-Site: same-origin" http://127.0.0.1:3000/api/news?limit=5

## 6. Install the timer + first manual run
    sudo cp /opt/cpio-news-fetcher/deploy/systemd/cv-ingester.* /etc/systemd/system/
    sudo systemd-analyze verify /etc/systemd/system/cv-ingester.service
    sudo systemctl daemon-reload && sudo systemctl enable --now cv-ingester.timer
    sudo systemctl start cv-ingester.service      # one manual run now
    journalctl -u cv-ingester.service --no-pager | tail -30

## 7. Confirm data flow
- Run the DB identity/compare check; confirm `MAX(published_on)` in cc_news advances.

## 8. GitHub secrets for auto-deploy
- Add repo secrets: VM_SSH_HOST (Elastic IP), VM_SSH_USER (ubuntu), VM_SSH_KEY (private key).
- Future pushes to main touching deploy/** auto-deploy via `.github/workflows/deploy-cv-vm.yml`.
````

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/cv-aws-setup.md
git commit -m "docs(news): AWS bring-up runbook for cv source"
```

---

## Self-Review Notes

- **Spec coverage:** topology (T3+T4), cv+Redis (T3), ingester+dedup+loud-fail (T2), cc_news mapping/dedup (T2), firewall/DB-host P0 (T1+T6), deploy (T5), runbook/division-of-labor (T6). Non-goals (backfill, cv-AI) correctly excluded.
- **Zero-touch:** `insert_db(create_table=False)` for cc_news; no ALTER/DROP anywhere.
- **Loud failure:** `run_production` exits 1 (DB), 2 (0-articles); Telegram best-effort.
- **Types consistent:** `get_existing_urls(conn, urls)->set`, `insert_db(rows, table, create_table, conn)`, `run_production(limit, table)->dict` used identically in Task 2 and consumed in Task 4 CLI.
