#!/usr/bin/env python3
"""
Korean Stock Market News Searcher

Fetches and displays news relevant to the Korean stock market (KOSPI, KOSDAQ, KRX)
from multiple RSS/web sources in both Korean and English.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus
import re
import html

import feedparser
import requests


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: Optional[str]   # ISO-8601 string for JSON serialisability
    summary: str = ""
    language: str = "en"

    def published_dt(self) -> Optional[datetime]:
        if not self.published:
            return None
        try:
            return datetime.fromisoformat(self.published)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# News sources
# ---------------------------------------------------------------------------

GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
)

SOURCES = [
    # --- Korean-language Google News queries ---
    {
        "url": GOOGLE_NEWS_RSS.format(
            query=quote_plus("코스피 주식"), hl="ko", gl="KR", ceid="KR:ko"
        ),
        "label": "Google News (KO) – 코스피",
        "lang": "ko",
    },
    {
        "url": GOOGLE_NEWS_RSS.format(
            query=quote_plus("코스닥"), hl="ko", gl="KR", ceid="KR:ko"
        ),
        "label": "Google News (KO) – 코스닥",
        "lang": "ko",
    },
    {
        "url": GOOGLE_NEWS_RSS.format(
            query=quote_plus("한국 증시 주식시장"), hl="ko", gl="KR", ceid="KR:ko"
        ),
        "label": "Google News (KO) – 한국증시",
        "lang": "ko",
    },
    # --- English-language Google News queries ---
    {
        "url": GOOGLE_NEWS_RSS.format(
            query=quote_plus("KOSPI stock market"), hl="en", gl="US", ceid="US:en"
        ),
        "label": "Google News (EN) – KOSPI",
        "lang": "en",
    },
    {
        "url": GOOGLE_NEWS_RSS.format(
            query=quote_plus("KOSDAQ Korea"), hl="en", gl="US", ceid="US:en"
        ),
        "label": "Google News (EN) – KOSDAQ",
        "lang": "en",
    },
    {
        "url": GOOGLE_NEWS_RSS.format(
            query=quote_plus("Korea stock exchange KRX"), hl="en", gl="US", ceid="US:en"
        ),
        "label": "Google News (EN) – KRX",
        "lang": "en",
    },
    # --- Korea Herald (English-language Korean news outlet) ---
    {
        "url": "https://www.koreaherald.com/rss/020200000000.xml",
        "label": "Korea Herald – Finance",
        "lang": "en",
    },
    # --- Yonhap News English RSS (Economy section) ---
    {
        "url": "https://en.yna.co.kr/RSS/economy.xml",
        "label": "Yonhap News – Economy",
        "lang": "en",
    },
]


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; KoreanStockNewsBot/1.0; "
        "+https://github.com/example/korean-stock-news)"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_published(entry) -> Optional[str]:
    """Return ISO-8601 string from a feedparser entry, or None."""
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.isoformat()
            except Exception:
                pass
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6])
            return dt.isoformat()
        except Exception:
            pass
    return None


def fetch_source(source: dict, timeout: int = 10) -> list[NewsItem]:
    """Fetch and parse a single RSS source."""
    items: list[NewsItem] = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        print(f"  [warning] Could not fetch {source['label']}: {exc}", file=sys.stderr)
        return items

    for entry in feed.entries:
        title = _clean_html(getattr(entry, "title", "") or "")
        url = getattr(entry, "link", "") or ""
        summary_raw = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )
        summary = _clean_html(summary_raw)[:300]

        if not title or not url:
            continue

        items.append(
            NewsItem(
                title=title,
                url=url,
                source=source["label"],
                published=_parse_published(entry),
                summary=summary,
                language=source["lang"],
            )
        )
    return items


def fetch_all(
    sources: list[dict],
    delay: float = 0.3,
) -> list[NewsItem]:
    """Fetch all sources with a small polite delay between requests."""
    all_items: list[NewsItem] = []
    for source in sources:
        all_items.extend(fetch_source(source))
        time.sleep(delay)
    return all_items


# ---------------------------------------------------------------------------
# Deduplication & filtering
# ---------------------------------------------------------------------------

def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Remove items with identical URLs."""
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = item.url.split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def filter_by_keywords(
    items: list[NewsItem], keywords: list[str]
) -> list[NewsItem]:
    """Keep only items whose title or summary contain at least one keyword."""
    kw_lower = [k.lower() for k in keywords]
    result = []
    for item in items:
        text = (item.title + " " + item.summary).lower()
        if any(k in text for k in kw_lower):
            result.append(item)
    return result


def sort_by_date(items: list[NewsItem]) -> list[NewsItem]:
    """Sort newest-first; items without a date go to the end."""
    def key(item: NewsItem):
        dt = item.published_dt()
        return dt if dt is not None else datetime.min

    return sorted(items, key=key, reverse=True)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

# ANSI colour helpers (degrade gracefully on non-TTY)
def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _color(code: str, text: str) -> str:
    if _supports_color():
        return f"\033[{code}m{text}\033[0m"
    return text


def _bold(t: str) -> str:
    return _color("1", t)


def _cyan(t: str) -> str:
    return _color("36", t)


def _yellow(t: str) -> str:
    return _color("33", t)


def _dim(t: str) -> str:
    return _color("2", t)


def print_items(items: list[NewsItem], max_summary_len: int = 200) -> None:
    if not items:
        print("No news items found.")
        return

    width = 78
    print(_bold("=" * width))
    print(_bold(f"  Korean Stock Market News  ({len(items)} articles)"))
    print(_bold("=" * width))

    for i, item in enumerate(items, start=1):
        # Header row
        lang_tag = f"[{item.language.upper()}]"
        print(f"\n{_bold(f'{i:>3}.')} {_cyan(item.title)}")

        # Meta line
        date_str = ""
        dt = item.published_dt()
        if dt:
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        print(
            _dim(f"     {lang_tag}  {item.source}")
            + ("  " + _yellow(date_str) if date_str else "")
        )

        # URL
        print(_dim(f"     {item.url}"))

        # Summary
        if item.summary:
            summary = item.summary[:max_summary_len]
            if len(item.summary) > max_summary_len:
                summary += "…"
            print(f"     {summary}")

    print("\n" + _bold("=" * width))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="korean_stock_news",
        description="Search the internet for Korean stock market news.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python korean_stock_news.py
  python korean_stock_news.py --lang ko --max 15
  python korean_stock_news.py --keyword 삼성 --keyword 반도체
  python korean_stock_news.py --json > news.json
""",
    )
    parser.add_argument(
        "--lang",
        choices=["ko", "en", "both"],
        default="both",
        help="Language of news to fetch (default: both)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=30,
        metavar="N",
        help="Maximum number of articles to display (default: 30)",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        dest="keywords",
        metavar="WORD",
        help="Filter by keyword (can be repeated, e.g. --keyword 삼성 --keyword 반도체)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of human-readable text",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Do not display article summaries",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Select sources
    if args.lang == "ko":
        active_sources = [s for s in SOURCES if s["lang"] == "ko"]
    elif args.lang == "en":
        active_sources = [s for s in SOURCES if s["lang"] == "en"]
    else:
        active_sources = SOURCES

    if not args.json:
        print(f"Fetching news from {len(active_sources)} sources…", file=sys.stderr)

    items = fetch_all(active_sources)
    items = deduplicate(items)

    if args.keywords:
        items = filter_by_keywords(items, args.keywords)

    items = sort_by_date(items)
    items = items[: args.max]

    if args.json:
        output = [asdict(item) for item in items]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_items(items, max_summary_len=0 if args.no_summary else 200)


if __name__ == "__main__":
    main()
