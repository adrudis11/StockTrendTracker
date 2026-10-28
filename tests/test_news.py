import io
import json
import zipfile
from datetime import UTC, datetime

from stocktrend.ingest import federal_register, gdelt, rss
from stocktrend.ingest.models import NewsItem, canonical_url, clean_text, dedupe

START = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
END = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


def item(url: str, title: str, feed: str = "f1", topics: list[str] | None = None) -> NewsItem:
    return NewsItem.make(
        url=url, source="rss", feed=feed, title=title, published_at=END, topics=topics or []
    )


# --- models -----------------------------------------------------------------


def test_canonical_url_drops_tracking() -> None:
    assert (
        canonical_url("HTTPS://Example.com/a/b/?utm_source=x&id=3&fbclid=y#top")
        == "https://example.com/a/b?id=3"
    )


def test_clean_text() -> None:
    assert clean_text("<p>Oil &amp; gas</p>  <b>up</b>") == "Oil & gas up"
    assert clean_text(None) is None
    long = clean_text("word " * 200, limit=50)
    assert long is not None and len(long) <= 50 and long.endswith("…")


def test_dedupe_by_url_and_title() -> None:
    items = [
        item("https://a.com/x", "Sanctions widened on Russian oil exports", topics=["sanctions"]),
        item("https://a.com/x?utm_medium=rss", "Other title", feed="f2", topics=["energy"]),
        item("https://b.com/y", "Sanctions widened on Russian oil exports!", feed="f3"),
        item("https://c.com/z", "Live updates", feed="f4"),
        item("https://d.com/z", "Live updates", feed="f5"),  # short titles don't merge
    ]
    out = dedupe(items)
    assert len(out) == 3
    assert out[0].topics == ["sanctions", "energy"]
    assert out[0].extra["also_in"] == ["f2", "f3"]


# --- RSS --------------------------------------------------------------------

GOOGLE_NEWS_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>EU agrees new Russia sanctions package - Reuters</title>
<link>https://news.google.com/rss/articles/abc?oc=5</link>
<pubDate>Tue, 28 Oct 2026 07:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;EU agrees new Russia sanctions package&lt;/a&gt;&amp;nbsp;Reuters</description>
<source url="https://www.reuters.com">Reuters</source></item>
<item><title>Too old - Reuters</title><link>https://news.google.com/rss/articles/old</link>
<pubDate>Mon, 26 Oct 2026 07:00:00 GMT</pubDate></item>
</channel></rss>"""

ECB_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>ECB</title>
<item><title>Monetary policy decisions</title><link>https://www.ecb.europa.eu/press/pr/1.html</link>
<pubDate>Tue, 27 Oct 2026 01:00:00 +0100</pubDate></item></channel></rss>"""


def test_google_news_parsing() -> None:
    out = list(rss.parse_feed(GOOGLE_NEWS_RSS, feed_id="gn", start=START, end=END, topics=["s"]))
    assert len(out) == 1
    n = out[0]
    assert n.title == "EU agrees new Russia sanctions package"
    assert n.publisher == "Reuters"
    assert n.summary is None  # just repeated the headline
    assert n.published_at == datetime(2026, 10, 28, 7, 0, tzinfo=UTC)
    assert n.topics == ["s"]


def test_official_feeds_match_by_day() -> None:
    # Stamped 2026-10-27 00:00 UTC: before START (09:00) but on the same day.
    assert list(rss.parse_feed(ECB_RSS, feed_id="ecb", start=START, end=END, official=True))
    assert not list(rss.parse_feed(ECB_RSS, feed_id="ecb", start=START, end=END))


def test_google_news_url() -> None:
    url = rss.google_news_url(
        "(tariff OR tariffs)", {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"}, 72
    )
    assert "when%3A3d" in url and "ceid=GB%3Aen" in url


# --- Federal Register -------------------------------------------------------


def test_federal_register_parsing() -> None:
    doc = {
        "title": "Imposition of Tariffs on Steel",
        "abstract": "<p>The President adjusts imports.</p>",
        "html_url": "https://www.federalregister.gov/documents/2026/10/28/2026-1/x",
        "publication_date": "2026-10-28",
        "type": "Presidential Document",
        "document_number": "2026-1",
        "agencies": [{"name": "Executive Office of the President"}, {"raw_name": "USTR"}],
    }
    (n,) = federal_register.parse_results([doc], "federal_register:presidential")
    assert n.summary == "The President adjusts imports."
    assert n.extra["agencies"] == ["Executive Office of the President", "USTR"]
    assert n.extra["official"] is True
    assert n.published_at == datetime(2026, 10, 28, tzinfo=UTC)


# --- GDELT ------------------------------------------------------------------


def _event_row(**values: str) -> str:
    cols = [""] * 61
    defaults = {
        0: "1",
        6: "RUSSIA",
        7: "RUS",
        16: "UNITED KINGDOM",
        17: "GBR",
        25: "1",
        26: "172",
        28: "17",
        29: "4",
        30: "-5.0",
        31: "20",
        33: "18",
        34: "-4.2",
        52: "Moscow, Russia",
        53: "RS",
        59: "20261028081500",
        60: "https://news.example.com/world/2026/10/28/russia-bans-british-defence-executives-12345.html",
    }
    names = {v: k for k, v in gdelt.EVENT_COLUMNS.items()}
    for name, value in values.items():
        defaults[names[name]] = value
    for pos, value in defaults.items():
        cols[pos] = value
    return "\t".join(cols)


def _export_zip(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20261028081500.export.CSV", "\n".join(rows) + "\n")
    return buf.getvalue()


def test_gdelt_export_parsing_and_top_events() -> None:
    rows = [
        _event_row(),
        _event_row(
            event_id="2",
            articles="99",
            actor2_country="RUS",  # domestic: dropped
            url="https://x.com/local/man-arrested-after-bar-fight-in-moscow",
        ),
        _event_row(
            event_id="3",
            articles="50",
            root_code="04",  # "consult": not newsworthy
            url="https://x.com/a/leaders-hold-talks-on-trade-matters",
        ),
        _event_row(event_id="4", articles="5", url="https://x.com/a/86b5-5b6b"),  # no title
    ]
    events = gdelt.parse_export(_export_zip(rows))
    assert len(events) == 4 and events["articles"].dtype.kind in "if"

    (top,) = gdelt.top_events(events, top_n=10)
    assert top.title == "Russia bans british defence executives"
    assert top.extra["event_type"] == "Coerce"
    assert top.extra["actor_countries"] == ["RUS", "GBR"]
    assert top.published_at == datetime(2026, 10, 28, 8, 15, tzinfo=UTC)

    agg = gdelt.aggregate(events)
    assert agg["events"].sum() == 4


def test_title_from_url() -> None:
    assert (
        gdelt.title_from_url("https://a.co.uk/news/26675864.us-embassy-worker-accused-of-spying/")
        == "Us embassy worker accused of spying"
    )
    assert gdelt.title_from_url("https://a.com/article/86b5-5b6b-bfd6-1234") is None
    assert (
        gdelt.title_from_url("https://a.com/f35-jets-sold-to-poland") == "F35 jets sold to poland"
    )


def test_file_stamps() -> None:
    stamps = gdelt.file_stamps(
        datetime(2026, 10, 28, 8, 7, tzinfo=UTC), datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    )
    assert stamps == ["20261028081500", "20261028083000", "20261028084500", "20261028090000"]


def test_gdelt_doc_parsing() -> None:
    data = json.loads(
        '{"articles": [{"url": "https://a.com/x", "title": "Tariffs rise ", '
        '"seendate": "20261028T081500Z", "domain": "a.com", "language": "English", '
        '"sourcecountry": "United States"}, {"url": "", "title": "no url"}]}'
    )
    (n,) = gdelt.parse_doc(data, "trade")
    assert n.title == "Tariffs rise" and n.feed == "gdelt_doc:trade"
    assert n.published_at == datetime(2026, 10, 28, 8, 15, tzinfo=UTC)
