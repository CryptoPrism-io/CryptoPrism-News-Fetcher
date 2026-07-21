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
