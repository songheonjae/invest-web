import asyncio, sys
from app.services.financials import get_financials

async def main():
    result = await get_financials("000660")
    annual = result.get("annual", [])
    quarterly = result.get("quarterly", [])
    print("=== 연간 ===")
    for r in annual:
        print(r)
    print("=== 분기 ===")
    for r in quarterly:
        print(r)

asyncio.run(main())
