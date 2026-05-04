import anthropic
import asyncio
import os
import re
import json
import urllib.parse
import httpx
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
_claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _classify_news_with_claude(articles: list[dict], stock_name: str = "") -> list[dict]:
    if not articles:
        return articles
    items_text = "\n".join([
        f"{i+1}. [{a['source']}] {a['title']}\n본문: {a.get('body','')[:200]}"
        for i, a in enumerate(articles)
    ])
    stock_hint = f" (분석 대상 종목: {stock_name})" if stock_name else ""
    prompt = f"""아래 주식 뉴스 기사들을 분석해서 JSON 배열로 응답해주세요{stock_hint}.
각 기사마다 아래 4개 필드를 반환하세요:
- summary: 2~3문장 핵심 요약 (본문 없으면 제목 기반으로 작성)
- sentiment: 긍정/중립/부정
- category: 실적/수주/규제/제품/인사/거시환경/노사/기타 중 하나
- relevance: 아래 기준을 순서대로 적용해라.
  1. 직접관련: 제목 또는 본문에 분석 대상 종목명이 명시적으로 등장하고, 해당 종목의 실적·사업·수급에 직접 영향을 주는 내용
  2. 간접관련: 종목명이 없어도 동일 업종·섹터 전체에 영향을 주는 내용 (예: 업종 규제, 금리 변동)
  3. 무관: 종목명이 없고 타사 전용 내용이거나 업종과 관계없는 일반 경제 뉴스. 타 기업 전용 기사(예: 경쟁사 인수합병, 타 기업 제재)는 반드시 무관으로 분류해라.

뉴스:
{items_text}

JSON 배열만 출력하세요. 설명 없이."""
    try:
        msg = _claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            for i, item in enumerate(parsed):
                if i < len(articles):
                    articles[i]["summary"] = item.get("summary", "")
                    articles[i]["sentiment"] = item.get("sentiment", "중립")
                    articles[i]["category"] = item.get("category", "기타")
                    articles[i]["relevance"] = item.get("relevance", "간접관련")
    except Exception:
        pass

    # 종목명이 제목에 없는데 직접관련으로 분류된 경우 간접관련으로 강제 하향
    if stock_name:
        for a in articles:
            if a.get("relevance") == "직접관련" and stock_name not in a.get("title", ""):
                a["relevance"] = "간접관련"

    for a in articles:
        a["grade"] = _compute_news_grade(a)
    return articles


async def _fetch_naver_article_body(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            res = await client.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")
        body = soup.select_one("div#newsct_article, div.newsct_article, div#articeBody, div.article_body")
        if body:
            return body.get_text(separator="\n", strip=True)[:500]
    except Exception:
        pass
    return ""


async def _get_naver_finance_news(ticker: str, limit: int = 5) -> list[dict]:
    url = f"https://finance.naver.com/item/news_news.naver?code={ticker}&page=1&sm=title_entity_id.basic&clusterId="
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")
        articles = []
        for row in soup.select("table.type5 tr"):
            title_tag = row.select_one("td.title a")
            date_tag = row.select_one("td.date")
            source_tag = row.select_one("td.info")
            if not title_tag:
                continue
            article_url = "https://finance.naver.com" + title_tag.get("href", "")
            body = await _fetch_naver_article_body(article_url)
            articles.append({
                "title": title_tag.get_text(strip=True),
                "url": article_url,
                "date": date_tag.get_text(strip=True) if date_tag else "",
                "source": source_tag.get_text(strip=True) if source_tag else "네이버",
                "body": body,
            })
            if len(articles) >= limit:
                break
        return articles
    except Exception:
        return []


async def _get_google_news_rss_ko(name: str, limit: int = 5) -> list[dict]:
    """한국어 Google News RSS - 종목명으로 검색"""
    if not name:
        return []
    q = urllib.parse.quote(f'"{name}" 주가')
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url, headers=headers)
        try:
            soup = BeautifulSoup(res.text, "xml")
        except Exception:
            soup = BeautifulSoup(res.text, "html.parser")
        articles = []
        for item in soup.find_all("item")[:limit]:
            title = item.find("title")
            link = item.find("link")
            pub_date = item.find("pubDate")
            source = item.find("source")
            if not title:
                continue
            articles.append({
                "title": title.get_text(strip=True),
                "url": link.get_text(strip=True) if link else "",
                "date": pub_date.get_text(strip=True)[:16] if pub_date else "",
                "source": source.get_text(strip=True) if source else "Google News",
                "body": "",
            })
        return articles
    except Exception:
        return []


async def _get_daum_news(ticker: str, limit: int = 5) -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.daum.net"}
    try:
        api_url = f"https://finance.daum.net/api/news/stock?page=1&perPage={limit}&symbolCode=A{ticker}&type=news"
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(api_url, headers={**headers, "Accept": "application/json"})
        data = res.json()
        articles = []
        for item in data.get("data", []):
            articles.append({
                "title": item.get("title", ""),
                "url": item.get("url") or item.get("pcUrl", ""),
                "date": item.get("datetime", "")[:10],
                "source": item.get("mediaName", "다음"),
                "body": "",
            })
        return articles
    except Exception:
        return []


async def _get_hankyung_news(name: str, limit: int = 5) -> list[dict]:
    if not name:
        return []
    query = urllib.parse.quote(name)
    url = f"https://search.hankyung.com/apps.frm/search.do?query={query}&type=news"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.hankyung.com"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")
        articles = []
        for item in soup.select("div.article-list li, ul.news-list li")[:limit]:
            title_tag = item.select_one("a.tit, h3 a, a")
            date_tag = item.select_one("span.date, span.time")
            if not title_tag or not title_tag.get_text(strip=True):
                continue
            href = title_tag.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.hankyung.com" + href
            articles.append({
                "title": title_tag.get_text(strip=True),
                "url": href,
                "date": date_tag.get_text(strip=True) if date_tag else "",
                "source": "한국경제",
                "body": "",
            })
        return articles
    except Exception:
        return []


async def _get_google_news_rss(query: str, limit: int = 5) -> list[dict]:
    if not query:
        return []
    q = urllib.parse.quote(f'"{query}" stock')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "xml")
        articles = []
        for item in soup.find_all("item")[:limit]:
            title = item.find("title")
            link = item.find("link")
            pub_date = item.find("pubDate")
            source = item.find("source")
            if not title:
                continue
            articles.append({
                "title": title.get_text(strip=True),
                "url": link.get_text(strip=True) if link else "",
                "date": pub_date.get_text(strip=True)[:16] if pub_date else "",
                "source": source.get_text(strip=True) if source else "Google News",
                "body": "",
            })
        return articles
    except Exception:
        return []


# 신뢰도 높은 언론사 (부분 문자열 매칭)
_TIER1_SOURCES = {
    # 국내
    "연합뉴스", "한국경제", "매일경제", "조선비즈", "이데일리",
    "서울경제", "머니투데이", "헤럴드경제", "뉴스1", "뉴시스",
    "파이낸셜뉴스", "한국일보", "중앙일보", "동아일보", "아시아경제",
    "전자신문", "디지털타임스",
    # 해외
    "Reuters", "Bloomberg", "WSJ", "Wall Street Journal",
    "Financial Times", "AP", "CNBC", "MarketWatch", "Barron",
}

_TIER3_SOURCES = {
    "블로그", "카페", "커뮤니티", "네이트", "네이버 블로그",
    "Seeking Alpha", "Motley Fool",
}


def _source_tier(source: str) -> int:
    """1=신뢰, 2=보통, 3=낮음"""
    if not source:
        return 2
    for s in _TIER1_SOURCES:
        if s.lower() in source.lower():
            return 1
    for s in _TIER3_SOURCES:
        if s.lower() in source.lower():
            return 3
    return 2


def _compute_news_grade(article: dict) -> str:
    category = article.get("category", "기타")
    relevance = article.get("relevance", "간접관련")
    tier = _source_tier(article.get("source", ""))

    if relevance == "무관" or tier == 3:
        return "C"
    a_categories = {"실적", "수주", "제품"}
    b_categories = {"규제", "인사", "거시환경", "노사"}

    if category in a_categories and relevance == "직접관련":
        return "A" if tier <= 2 else "B"
    if category in a_categories or relevance == "직접관련":
        return "A" if tier == 1 else "B"
    if category in b_categories:
        return "B" if tier <= 2 else "C"
    return "C"


def _title_similarity(t1: str, t2: str) -> float:
    s1, s2 = set(t1), set(t2)
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / max(len(s1), len(s2))


def _deduplicate(articles: list[dict], threshold: float = 0.7) -> list[dict]:
    result = []
    for article in articles:
        title = article["title"]
        if not any(_title_similarity(title, r["title"]) >= threshold for r in result):
            result.append(article)
    return result


async def get_stock_news(ticker: str, name: str = "", limit: int = 5) -> list[dict]:
    google, naver_finance, daum, hankyung = await asyncio.gather(
        _get_google_news_rss_ko(name, limit),
        _get_naver_finance_news(ticker, limit),
        _get_daum_news(ticker, limit),
        _get_hankyung_news(name, limit),
    )
    seen = set()
    merged = []
    for article in google + naver_finance + daum + hankyung:
        if article["title"] not in seen:
            seen.add(article["title"])
            merged.append(article)
        if len(merged) >= limit * 3:
            break
    merged = _deduplicate(merged)
    # tier1 소스 앞으로 정렬 후 limit * 2 개 선택
    merged.sort(key=lambda a: _source_tier(a.get("source", "")))
    merged = merged[:limit * 2]
    return _classify_news_with_claude(merged, stock_name=name)


async def get_foreign_news(ticker: str, name: str = "", limit: int = 5) -> list[dict]:
    yf_articles = []
    try:
        news_list = yf.Ticker(ticker).news or []
        for item in news_list[:limit]:
            content = item.get("content", {})
            yf_articles.append({
                "title": content.get("title", item.get("title", "")),
                "url": content.get("canonicalUrl", {}).get("url", "") or item.get("link", ""),
                "date": content.get("pubDate", "")[:10] if content.get("pubDate") else "",
                "source": content.get("provider", {}).get("displayName", "") or "Yahoo Finance",
                "body": "",
            })
    except Exception:
        pass

    google_articles = await _get_google_news_rss(name or ticker, limit)

    seen = set()
    merged = []
    for article in yf_articles + google_articles:
        if article["title"] not in seen:
            seen.add(article["title"])
            merged.append(article)
        if len(merged) >= limit * 3:
            break
    merged = _deduplicate(merged)
    merged.sort(key=lambda a: _source_tier(a.get("source", "")))
    merged = merged[:limit * 2]
    return _classify_news_with_claude(merged, stock_name=name or ticker)
