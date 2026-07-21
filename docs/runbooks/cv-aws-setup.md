# cv news source — AWS bring-up runbook

## 0. DB facts (verified 2026-07-21 via db-identity-check workflow)
- **Server (private VPC IP): `172.31.82.167`** — default AWS VPC range `172.31.0.0/16`.
- **PostgreSQL 16.13, self-managed on EC2 (NOT RDS)** (`looks_like_rds: False`).
- Publicly reachable via the `DB_HOST` GitHub secret endpoint (that's how Actions connects);
  from inside the VPC use the **private IP `172.31.82.167`**.
- `cc_news`: 383,706 rows.

## 1. Launch EC2 (cv host)
- **t3.small, Ubuntu 24.04, 20 GB gp3.**
- **Launch it in the SAME VPC as the DB (`172.31.0.0/16`)** so it reaches Postgres
  privately at `172.31.82.167:5432` — no public DB exposure needed.
- Attach an **Elastic IP** (stable identity for SSH).
- Security group inbound: **SSH (22) from your IP only**. Outbound: 443 + 5432.

## 2. Allowlist the VM on Postgres  ← migration-risk step (self-managed, same-VPC)
Because the DB is self-managed Postgres on EC2 (not RDS), do BOTH:
- **Security group:** on the DB instance's security group, allow inbound **5432 from
  the cv VM's security group** (or the cv VM's private IP `172.31.x.x`).
- **pg_hba.conf / listen:** ensure the DB accepts connections from the VM's subnet
  (it already accepts external Actions connections, so likely fine — confirm the
  VM's VPC CIDR is covered).
- **Verify from the VM BEFORE proceeding:**
      psql "host=172.31.82.167 port=5432 user=<DB_USER> dbname=dbcp" -c "select 1"
  (Do not trust "app boots" — the deploy skill flags a past migration outage from
  exactly a missed firewall rule.)

## 3. Install Docker + clone
    curl -fsSL https://get.docker.com | sh
    sudo git clone https://github.com/CryptoPrism-io/CryptoPrism-News-Fetcher /opt/cpio-news-fetcher

## 4. Configure env
    sudo cp /opt/cpio-news-fetcher/deploy/systemd/cv-ingester.env.example /etc/cv-ingester.env
    sudo nano /etc/cv-ingester.env
    #   DB_HOST=172.31.82.167   (private, same-VPC)
    #   DB_USER / DB_PASSWORD / DB_NAME=dbcp   (from GitHub secrets)
    #   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (optional, for failure pings)

## 5. Bring up cv + Redis
    cd /opt/cpio-news-fetcher/deploy/cv && sudo docker compose up -d --build
    curl -s -H "Sec-Fetch-Site: same-origin" http://127.0.0.1:3000/api/news?limit=5
    # If the remote git build context fails, clone cv locally and point build.context at it.

## 6. Install the timer + first manual run
    sudo cp /opt/cpio-news-fetcher/deploy/systemd/cv-ingester.* /etc/systemd/system/
    sudo systemd-analyze verify /etc/systemd/system/cv-ingester.service
    sudo systemctl daemon-reload && sudo systemctl enable --now cv-ingester.timer
    sudo systemctl start cv-ingester.service      # one manual run now
    journalctl -u cv-ingester.service --no-pager | tail -30
    # Expect: "feed=... new=... inserted=... body>=300=..." and exit 0.

## 7. Confirm data flow
- Re-run the DB identity/compare check (Actions) or query directly; confirm
  `MAX(published_on)` in cc_news advances past the run time.

## 8. GitHub secrets for auto-deploy
- Add repo secrets: `VM_SSH_HOST` (Elastic IP), `VM_SSH_USER` (ubuntu),
  `VM_SSH_KEY` (private key).
- After merging `feat/cv-production-source` to `main`, pushes touching `deploy/**`
  or `src/news_fetcher/**` auto-deploy via `.github/workflows/deploy-cv-vm.yml`.

## Division of labor
- **Built (in-repo, done):** ingester prod mode, docker-compose, systemd units,
  deploy workflow, this runbook.
- **You do (AWS creds required):** steps 1, 2, 8 (launch EC2 in the VPC, allowlist,
  add SSH secrets). Steps 3–7 are copy-paste on the VM.
