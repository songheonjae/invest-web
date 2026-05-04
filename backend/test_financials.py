import httpx, asyncio
from bs4 import BeautifulSoup

async def test(ticker="000660"):
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url, headers=headers)
    html = res.content.decode("euc-kr", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("div.section.cop_analysis")
    if not section:
        print("섹션 없음")
        return

    rows = section.select("tr")
    print(f"총 {len(rows)}개 행\n")

    # 처음 5행 원본 출력
    for i, row in enumerate(rows[:6]):
        cells = [td.get_text(strip=True) for td in row.select("th, td")]
        cells = [c for c in cells if c]
        print(f"행{i}: {cells}")

    print("\n--- 숫자 행 (data_rows) ---")
    data_rows = []
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        cells = [c for c in cells if c]
        if cells and any(c.replace(",","").replace(".","").replace("-","").isdigit() for c in cells[1:]):
            data_rows.append(cells)
            if len(data_rows) <= 3:
                print(f"  {cells}")

asyncio.run(test())
