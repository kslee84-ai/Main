#!/usr/bin/env python3
"""
Korean Stock Market News Searcher

Fetches and displays news relevant to the Korean stock market (KOSPI, KOSDAQ, KRX)
from multiple RSS/web sources in both Korean and English.

Use --analyze to get an AI-generated market summary with token usage stats.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
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

# Claude model used for AI analysis
_CLAUDE_MODEL = "claude-opus-4-7"
_CONTEXT_WINDOW = 1_000_000  # Opus 4.7 context window


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
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_published(entry) -> Optional[str]:
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


def fetch_all(sources: list[dict], delay: float = 0.3) -> list[NewsItem]:
    all_items: list[NewsItem] = []
    for source in sources:
        all_items.extend(fetch_source(source))
        time.sleep(delay)
    return all_items


# ---------------------------------------------------------------------------
# Deduplication & filtering
# ---------------------------------------------------------------------------

def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = item.url.split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def filter_by_keywords(items: list[NewsItem], keywords: list[str]) -> list[NewsItem]:
    kw_lower = [k.lower() for k in keywords]
    result = []
    for item in items:
        text = (item.title + " " + item.summary).lower()
        if any(k in text for k in kw_lower):
            result.append(item)
    return result


def sort_by_date(items: list[NewsItem]) -> list[NewsItem]:
    def key(item: NewsItem):
        dt = item.published_dt()
        return dt if dt is not None else datetime.min
    return sorted(items, key=key, reverse=True)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

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


def _green(t: str) -> str:
    return _color("32", t)


def _dim(t: str) -> str:
    return _color("2", t)


def _hyperlink(url: str, text: str) -> str:
    """Wrap text in an OSC 8 terminal hyperlink (clickable in modern terminals)."""
    if _supports_color():
        return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"
    return text


# ---------------------------------------------------------------------------
# AI analysis & token tracking
# ---------------------------------------------------------------------------

def analyze_with_claude(items: list[NewsItem]) -> tuple[str, object]:
    """
    Send news headlines to Claude for a market summary.
    Returns (summary_text, usage_object).
    Requires ANTHROPIC_API_KEY env var.
    """
    try:
        import anthropic
    except ImportError:
        print(
            "  [error] anthropic package not installed. Run: pip install anthropic",
            file=sys.stderr,
        )
        return "", None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "  [error] ANTHROPIC_API_KEY environment variable not set.",
            file=sys.stderr,
        )
        return "", None

    client = anthropic.Anthropic(api_key=api_key)

    headlines = "\n".join(
        f"- [{item.language.upper()}] {item.title}"
        for item in items[:25]
    )

    prompt = (
        "아래는 오늘의 한국 주식시장 뉴스 헤드라인입니다.\n"
        "Below are today's Korean stock market news headlines.\n\n"
        f"{headlines}\n\n"
        "Please provide:\n"
        "1. A 2-3 sentence market summary in Korean (한국어 요약)\n"
        "2. A 2-3 sentence market summary in English\n"
        "3. The top 3 key themes or movers you see in these headlines"
    )

    try:
        response = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return text, response.usage
    except Exception as exc:
        print(f"  [error] Claude API call failed: {exc}", file=sys.stderr)
        return "", None


def print_token_stats(usage, label: str = "AI Analysis") -> None:
    """Print a token usage summary bar."""
    if usage is None:
        return

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    total_used = input_tokens + output_tokens
    remaining = max(0, _CONTEXT_WINDOW - total_used)
    pct_used = total_used / _CONTEXT_WINDOW * 100

    width = 78
    bar_width = 40
    filled = max(1, int(bar_width * total_used / _CONTEXT_WINDOW))
    bar = "█" * filled + "░" * (bar_width - filled)

    print()
    print(_bold("─" * width))
    print(_bold(f"  Token Usage  ·  {label}  ·  Model: {_CLAUDE_MODEL}"))
    print(_bold("─" * width))
    print(
        f"  Input   : {_yellow(f'{input_tokens:>10,}')} tokens"
    )
    print(
        f"  Output  : {_yellow(f'{output_tokens:>10,}')} tokens"
    )
    print(
        f"  Total   : {_bold(f'{total_used:>10,}')} tokens  ({pct_used:.3f}% of context window)"
    )
    print(
        f"  Remaining: {_green(f'{remaining:>9,}')} tokens  (context window: {_CONTEXT_WINDOW:,})"
    )
    print(f"\n  [{_green(bar[:filled]) + _dim(bar[filled:])}]")
    print(_bold("─" * width))


# ---------------------------------------------------------------------------
# Article display
# ---------------------------------------------------------------------------

def print_items(items: list[NewsItem], max_summary_len: int = 200) -> None:
    if not items:
        print("No news items found.")
        return

    width = 78
    print(_bold("=" * width))
    print(_bold(f"  Korean Stock Market News  ({len(items)} articles)"))
    print(_bold("=" * width))

    for i, item in enumerate(items, start=1):
        lang_tag = f"[{item.language.upper()}]"

        # Title as a clickable hyperlink (OSC 8) in supporting terminals
        clickable_title = _hyperlink(item.url, item.title)
        print(f"\n{_bold(f'{i:>3}.')} {_cyan(clickable_title)}")

        # Meta line
        date_str = ""
        dt = item.published_dt()
        if dt:
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        print(
            _dim(f"     {lang_tag}  {item.source}")
            + ("  " + _yellow(date_str) if date_str else "")
        )

        # Explicit link line so the URL is always visible as plain text too
        print(f"     {_dim('Link:')} {item.url}")

        # Summary
        if item.summary and max_summary_len > 0:
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
        epilog=f"""
Examples:
  python korean_stock_news.py
  python korean_stock_news.py --lang ko --max 15
  python korean_stock_news.py --keyword 삼성 --keyword 반도체
  python korean_stock_news.py --analyze          # AI summary + token usage
  python korean_stock_news.py --json > news.json

Token tracking (--analyze) requires ANTHROPIC_API_KEY and the anthropic package:
  pip install anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  python korean_stock_news.py --analyze
  (uses {_CLAUDE_MODEL}, context window {_CONTEXT_WINDOW:,} tokens)
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
        help="Filter by keyword (can be repeated)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help=(
            "Use Claude AI to summarize the market news and display token usage. "
            "Requires ANTHROPIC_API_KEY."
        ),
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
        return

    print_items(items, max_summary_len=0 if args.no_summary else 200)

    if args.analyze:
        print(f"\nAsking {_CLAUDE_MODEL} for a market summary…", file=sys.stderr)
        summary, usage = analyze_with_claude(items)
        if summary:
            width = 78
            print()
            print(_bold("=" * width))
            print(_bold("  AI Market Summary"))
            print(_bold("=" * width))
            print(summary)
        print_token_stats(usage, label="Market Summary")


if __name__ == "__main__":
    main()
