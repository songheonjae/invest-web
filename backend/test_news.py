import httpx, asyncio, urllib.parse
from bs4 import BeautifulSoup

async def test():
    name = "하나마이크론"
    q = urllib.parse.quote(f'"{name}" 주가')
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "xml")
    for item in soup.find_all("item")[:5]:
        title = item.find("title")
        source = item.find("source")
        pub = item.find("pubDate")
        print(f"[{source.get_text(strip=True) if source else '?'}] {title.get_text(strip=True) if title else '?'} ({pub.get_text()[:10] if pub else ''})")

asyncio.run(test())
