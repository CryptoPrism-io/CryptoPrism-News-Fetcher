# Design: cryptocurrency.cv as the Production News Source

**Date:** 2026-07-21
**Author:** Yogesh Sahu (@CryptoPrism-io)
**Status:** Approved (design)

## Problem

The hourly CoinDesk/CryptoCompare news fetch has been silently failing since
~2026-07-11. Root cause: the free CoinDesk tier is capped at **100 API
calls/month**; once exhausted it returns errors as HTTP 200 with empty `Data`,
which the old code treated as "no news" and exited green. `cc_news` has been
dark for 10+ days with systematic monthly gaps going back months.

A code fix (loud failure) has landed (PR #9), but no code beats the 100/mo cap.
Evaluation of the open-source aggregator **cryptocurrency.cv** (self-hosted)
showed it is a viable free replacement:

- Free, no API key; 200+ sources; unlimited when self-hosted.
- Full access self-hosted via the `Sec-Fetch-Site: same-origin` header (bypasses
  the hosted 3-article free-tier cap — header-gated, not a paywall).
- `/api/news/extract` (POST) returns full article body text (median ~8,000
  chars in testing) — clears sentiment.py's `MIN_BODY_LENGTH = 300`.
- Measured body quality **beats** CoinDesk's stored bodies (cv median 7,994
  chars / 82% ≥300 vs CoinDesk 457 chars / 62% ≥300 on 2026-07-10).

Caveat surfaced in eval: the self-hosted feed's 200-feed fan-out is unreliable
without a cache (returns 0 articles on cold in-memory cache). **Redis fixes
this** — cv supports plain `REDIS_URL` (the `redis` npm client).

## Goal

Run cryptocurrency.cv as a reliable, always-on, self-owned production news
source on AWS, feeding `cc_news` hourly with full article bodies, alongside the
existing (mostly-dormant) CoinDesk fetch. No silent failures.

## Non-goals

- Historical backfill of the Jun/Jul 2026 gap (cv has no 2026 archive; tracked
  separately).
- Using cv's AI features (summaries/sentiment/translation) — we only use
  `/api/news` + `/api/news/extract`.
- Retiring CoinDesk (kept as a hybrid/fallback per decision below).

## Architecture

AWS EC2 **t3.small** (2 GB) running Docker, in the AWS account we migrated to.

```
EC2 t3.small (Docker) — Elastic IP
  docker-compose:
    cv    (nirholas/cryptocurrency.cv, pinned commit)  bind 127.0.0.1:3000
      └─ REDIS_URL=redis://redis:6379
    redis (redis:7-alpine, named volume)               internal only
  host systemd timer (hourly):
    ingester  fetch_ccv.py --db --table cc_news
      ├─ GET  http://127.0.0.1:3000/api/news        (list, Sec-Fetch-Site hdr)
      ├─ POST http://127.0.0.1:3000/api/news/extract (full body per new URL)
      └─ psycopg2 → Postgres cc_news  (dedup ON CONFLICT (url) DO NOTHING)
```

- **cv is never public** — bound to localhost; only SSH (our IP) is open inbound.
- Redis provides the warm cache that makes the feed reliable.
- Ingester runs on the VM (decision: cron/systemd), reaching cv on localhost and
  Postgres over the network.

### Components (each independently testable)

1. **`docker-compose.yml`** — cv + redis. Input: env (`REDIS_URL`, optional
   `GROQ_API_KEY`). Output: cv serving on `127.0.0.1:3000`. Depends on: Docker,
   the cv repo (git submodule or clone pinned to a commit).
2. **`fetch_ccv.py` (prod mode)** — extend existing ingester with robust
   `--db --table cc_news`, url-dedup pre-check (skip extract for URLs already in
   `cc_news`), body floor, structured logging, non-zero exit on hard failure.
   Input: `CCV_BASE_URL`, `DB_*`. Output: rows inserted into `cc_news`.
3. **systemd unit + timer** — `cv-ingester.service` + `.timer` (hourly). Input:
   an env file with `DB_*` + `CCV_BASE_URL`. Output: hourly runs, journald logs.
4. **Deploy workflow** — GitHub Actions SSH deploy (fleet `neelkanth_c3`
   pattern): SSH to VM → update cv + repo → `docker compose up -d --build` →
   `systemctl restart cv-ingester.timer`. Secrets: `VM_SSH_HOST`, `VM_SSH_USER`,
   `VM_SSH_KEY`.
5. **Runbook** — `docs/` step-by-step for the AWS-side one-time setup.

## Data mapping → `cc_news` (hybrid with CoinDesk)

| cc_news column | cv source |
|---|---|
| `id` (BIGINT PK) | `signed64(sha1(link))` |
| `url` (UNIQUE) | `link` |
| `title` | `title` |
| `published_on` | `pubDate` (ISO, UTC) |
| `body` | extracted `content` from `/api/news/extract` |
| `body_length` | `len(body)` |
| `source` | `sourceKey` |
| `source_name` | `source` |
| `categories` | `category` (single; pipe-taxonomy not available) |
| `has_image` / `imageurl` | `image` |
| `upvotes`/`downvotes` | 0 |
| `fetched_at` | now() UTC |

- **Dedup by `url`** (`ON CONFLICT (url) DO NOTHING`). cv and CoinDesk coexist;
  cv `id`s are full 64-bit hashes, CoinDesk `id`s are small ints → no PK clash.
- Pre-check existing URLs to avoid re-extracting known articles (saves work).

## Failure visibility (no repeat of the silent CoinDesk failure)

- Ingester exits non-zero on: cv unreachable, DB unreachable, or 0 articles from
  a warm cache (suspicious).
- Emit an hourly summary line to journald; optional Telegram ping (TRISHULA/QA
  bot) on hard failure — reuses existing `TELEGRAM_*` env.
- Weekly keep-alive note: systemd timers don't suffer GitHub's 60-day cron
  auto-disable, but the deploy workflow does — add a keep-alive or accept manual
  re-enable.

## Security / firewall (migration-risk item — P0)

**The deploy skill flags a past GCP→AWS migration that caused a multi-day
outage from a firewall lockdown. This is the highest-risk area.**

- **P0 — verify current DB host/type BEFORE deploy.** Local `.env` says
  `34.55.195.199` (GCP) but the working source of truth is the GitHub Actions
  `DB_HOST` secret (proven: the eval workflows connected & read `cc_news` on
  2026-07-21). First implementation step runs `SELECT inet_server_addr(),
  version()` via Actions to confirm whether Postgres is still GCE or now AWS RDS.
  - If **AWS RDS**: allowlist the VM via the RDS security group (ideally same VPC
    → private, no public exposure).
  - If **still GCP GCE**: add the VM's Elastic IP to the GCP firewall inbound.
- **Elastic IP** on the EC2 instance so the allowlisted IP is stable.
- **Security group:** inbound = SSH from our IP only. Outbound = 443 (RSS +
  extract) + 5432 (Postgres). cv/Redis never exposed.
- **Post-deploy check:** explicitly verify VM→Postgres connectivity (`SELECT 1`)
  and cv→RSS reachability before enabling the timer — do not trust "app boots".

## Division of labor

- **Built here:** `docker-compose.yml`, prod `fetch_ccv.py`, systemd unit+timer,
  deploy workflow, `.env` templates, AWS runbook.
- **User does (AWS creds required):** launch EC2 t3.small + Elastic IP, allowlist
  the IP on the DB, add SSH deploy secrets (`VM_SSH_HOST/USER/KEY`).

## Testing

- `fetch_ccv.py`: unit-test `normalize`/`link_to_id`/dedup against fixtures;
  integration test against a local cv container writing to a throwaway table.
- Deploy: dry-run compose locally; first VM bring-up via runbook, then verify
  one manual timer run inserts into `cc_news` and `MAX(published_on)` advances.
- Rollback: `docker compose down` + disable timer (no data migration to undo;
  cc_news writes are additive and url-deduped).

## Open questions (resolve in plan)

1. Current DB host/type post-migration (P0 — verify first).
2. Same-VPC RDS vs cross-cloud (determines allowlist mechanism + latency).
3. cv repo as git submodule vs pinned clone in the deploy.
