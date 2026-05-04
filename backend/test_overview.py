import httpx, asyncio
from bs4 import BeautifulSoup

async def test():
    url = "https://finance.naver.com/item/coinfo.naver?code=067310&target=company_info"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
    async with httpx.AsyncClient(timeout=8) as client:
        res = await client.get(url, headers=headers)
    html = res.content.decode("euc-kr", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    for p in soup.find_all("p")[:10]:
        text = p.get_text(strip=True)
        if len(text) > 20:
            print(repr(text[:150]))

asyncio.run(test())
