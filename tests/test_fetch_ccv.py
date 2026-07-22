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


def _page_size(path):
    """Extract whichever size param a request used (limit= or per_page=)."""
    for key in ("per_page=", "limit="):
        if key in path:
            return int(path.split(key)[1].split("&")[0])
    return 0


def test_feed_requests_respect_cv_max_limit(monkeypatch):
    """cv's schema caps limit/per_page at 100; more returns HTTP 400."""
    calls = []

    def fake_get(path):
        calls.append(path)
        return {"articles": []}

    monkeypatch.setattr(mod, "_get", fake_get)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    mod.fetch_articles(limit=200)

    assert calls, "no requests were made"
    for c in calls:
        assert _page_size(c) <= 100, f"request exceeds cv max (would 400): {c}"


def test_unfiltered_feed_paginates_for_breadth(monkeypatch):
    """The first page is recency-dominated by a few high-frequency feeds, so we
    must walk several pages to reach a wider spread of sources."""
    import re

    calls = []

    def fake_get(path):
        calls.append(path)
        m = re.search(r"page=(\d+)", path)
        if not m:
            return {"articles": []}
        p = int(m.group(1))
        return {"articles": [{"link": f"https://x/{p}-{i}"} for i in range(100)]}

    monkeypatch.setattr(mod, "_get", fake_get)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    arts = mod.fetch_articles(limit=200)

    pages = [c for c in calls if "page=" in c]
    assert len(pages) >= 2, f"expected multi-page walk, got: {pages}"
    assert len(arts) == 200, f"expected 200 assembled articles, got {len(arts)}"


def test_get_existing_urls_filters(monkeypatch):
    class FakeCur:
        def execute(self, sql, params=None):
            self._rows = [("https://x.com/a",)]

        def fetchall(self):
            return self._rows

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

    seen = mod.get_existing_urls(FakeConn(), ["https://x.com/a", "https://x.com/b"])
    assert seen == {"https://x.com/a"}
