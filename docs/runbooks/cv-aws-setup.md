# cv news source — AWS bring-up runbook

## 0. DB facts (corrected 2026-07-22)
- **Amazon RDS `dbcp-aws`** — PostgreSQL 16.13. Endpoint
  `dbcp-aws.ci348o64i4ep.us-east-1.rds.amazonaws.com`, SG `sg-0ea3ffe343953e7f3`,
  VPC `vpc-037b71e236c21355a`, us-east-1b, private ENI `172.31.82.167`. Publicly
  accessible (how Actions connects). (An earlier `looks_like_rds: False` was a
  false negative — RDS PG16 reports a generic build string. It IS RDS.)
- Credentials live in Secrets Manager `/dbcp-aws/postgres` (`dbcp_admin` / db `dbcp`).
- `cc_news`: 383,706 rows.

## AS-DEPLOYED (provisioned via AWS CLI 2026-07-22)
This runbook was executed via CLI, not by hand. Resources created:
- EC2 **`i-02b1b632026b06d9b`** — t3.small, Ubuntu 24.04, 30 GB, IMDSv2, public
  `13.218.44.198` / private `172.31.91.167`, subnet `subnet-0f7e2e3254ca1191b` (us-east-1b).
- SG **`sg-067bc66da8e718509`** (`cv-ingester-sg`) — no inbound; egress all.
- IAM **`cv-ingester-role`** / profile **`cv-ingester-profile`** — SSM core +
  read-only on the `/dbcp-aws/postgres` secret. **Keyless (SSM), no SSH.**
- RDS ingress rule **`sgr-0ca444e6640209445`** — `cv-ingester-sg` → RDS 5432.
- 8 GB swap on the VM so the cv Next.js build (wants ~8 GB) survives on 2 GB RAM.

## Operate via SSM (no SSH)
    aws ssm start-session --target i-02b1b632026b06d9b        # interactive shell
    # or one-off:
    aws ssm send-command --instance-ids i-02b1b632026b06d9b \
      --document-name AWS-RunShellScript \
      --parameters commands='journalctl -u cv-ingester.service --no-pager | tail -30'

## Redeploy after a code change
    # SSM the VM: pull latest + re-run bring-up
    cd /opt/cpio-news-fetcher && git pull && bash deploy/vm-bringup.sh

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
