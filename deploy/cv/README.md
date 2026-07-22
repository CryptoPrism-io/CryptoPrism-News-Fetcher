# cv self-host (cv + Redis)

On the EC2 VM:

    cd deploy/cv
    docker compose up -d --build
    curl -s -H "Sec-Fetch-Site: same-origin" http://127.0.0.1:3000/api/news?limit=5

cv is bound to localhost only. Redis provides the warm cache that keeps the
200-feed aggregation from returning 0 articles (the flaky-feed problem seen
during evaluation with in-memory cache).

If the remote git build context fails, clone the repo on the VM and point the
compose `build.context` at the local path instead.
