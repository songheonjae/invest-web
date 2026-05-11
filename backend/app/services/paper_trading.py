"""
모의투자 서비스 — SQLite 영구 저장
현금은 KRW 단일 계좌. 해외 종목 거래 시 실시간 환율(USD→KRW) 변환 후 차감.
"""
import asyncio
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "paper_trading.db"

# ── 환율 캐시 (5분 TTL) ───────────────────────────────────────
_rate_cache: dict = {"rate": 1350.0, "ts": 0.0}
_RATE_TTL = 300

# ── 가격 캐시 (30초 TTL) ──────────────────────────────────────
_price_cache: dict[str, tuple[float, float]] = {}
_PRICE_TTL = 30

# KIS API 동시 호출 수 제한 (rate limit 방지)
_kis_sem = asyncio.Semaphore(2)


async def _get_usd_krw_rate() -> float:
    """USD/KRW 환율. yfinance로 가져오고 실패 시 캐시 또는 1350 반환."""
    now = time.time()
    if now - _rate_cache["ts"] < _RATE_TTL:
        return _rate_cache["rate"]
    try:
        import asyncio
        import yfinance as yf

        def _fetch():
            hist = yf.Ticker("KRW=X").history(period="2d")
            return float(hist["Close"].dropna().iloc[-1]) if not hist.empty else None

        rate = await asyncio.to_thread(_fetch)
        if rate and 900 < rate < 2000:
            _rate_cache["rate"] = rate
            _rate_cache["ts"] = now
            return rate
    except Exception:
        pass
    _rate_cache["ts"] = now  # 실패해도 TTL 갱신해서 재시도 빈도 줄이기
    return _rate_cache["rate"]


def _to_krw(amount_native: float, market: str, rate: float) -> float:
    """USD → KRW 변환 (KR 종목은 그대로)."""
    return round(amount_native * rate) if market == "US" else round(amount_native)


# ── DB 초기화 ──────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id           INTEGER PRIMARY KEY,
                initial_cash REAL NOT NULL,
                cash         REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_positions (
                market        TEXT NOT NULL,
                ticker        TEXT NOT NULL,
                name          TEXT NOT NULL,
                avg_price     REAL NOT NULL,
                quantity      INTEGER NOT NULL,
                avg_cost_krw  REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (market, ticker)
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at           TEXT NOT NULL,
                trade_type           TEXT NOT NULL,
                market               TEXT NOT NULL,
                ticker               TEXT NOT NULL,
                name                 TEXT NOT NULL,
                price                REAL NOT NULL,
                quantity             INTEGER NOT NULL,
                amount               REAL NOT NULL,
                amount_krw           REAL NOT NULL DEFAULT 0,
                usd_krw_rate         REAL NOT NULL DEFAULT 1,
                realized_profit      REAL,
                realized_profit_rate REAL
            );
        """)
        # 기존 DB에 컬럼이 없으면 추가 (마이그레이션)
        try:
            con.execute("ALTER TABLE paper_trades ADD COLUMN amount_krw REAL NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE paper_trades ADD COLUMN usd_krw_rate REAL NOT NULL DEFAULT 1")
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE paper_account ADD COLUMN deposited REAL NOT NULL DEFAULT 0")
        except Exception:
            pass
        # avg_cost_krw: 매수 당시 환율 기준 주당 원화 비용 (환율 손익 정확 반영)
        try:
            con.execute("ALTER TABLE paper_positions ADD COLUMN avg_cost_krw REAL NOT NULL DEFAULT 0")
            # 기존 포지션을 거래 내역에서 재구성
            positions = con.execute("SELECT market, ticker FROM paper_positions").fetchall()
            for p in positions:
                market, ticker = p["market"], p["ticker"]
                trades = con.execute(
                    "SELECT trade_type, quantity, amount_krw FROM paper_trades "
                    "WHERE market=? AND ticker=? AND trade_type IN ('BUY','SELL') ORDER BY id",
                    (market, ticker),
                ).fetchall()
                total_qty = 0
                total_cost_krw = 0.0
                for t in trades:
                    if t["trade_type"] == "BUY":
                        total_cost_krw += t["amount_krw"]
                        total_qty += t["quantity"]
                    elif t["trade_type"] == "SELL" and total_qty:
                        avg = total_cost_krw / total_qty
                        total_cost_krw -= avg * t["quantity"]
                        total_qty -= t["quantity"]
                avg_cost_krw = total_cost_krw / total_qty if total_qty else 0
                con.execute(
                    "UPDATE paper_positions SET avg_cost_krw=? WHERE market=? AND ticker=?",
                    (avg_cost_krw, market, ticker),
                )
        except Exception:
            pass


_init_db()


# ── 계좌 헬퍼 ─────────────────────────────────────────────────
def _ensure_account(con: sqlite3.Connection, initial_cash: float = 10_000_000) -> sqlite3.Row:
    row = con.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
    if row is None:
        con.execute(
            "INSERT INTO paper_account (id, initial_cash, cash) VALUES (1, ?, ?)",
            (initial_cash, initial_cash),
        )
        row = con.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
    return row


# ── 현재가 조회 ────────────────────────────────────────────────
async def _fetch_price(market: str, ticker: str) -> float:
    cache_key = f"{market}:{ticker}"
    now = time.time()
    cached = _price_cache.get(cache_key)
    if cached and now - cached[1] < _PRICE_TTL:
        return cached[0]

    if market == "KR":
        async with _kis_sem:
            from app.services.kis_fundamental import _raw_price_and_fundamental
            stock, _ = await _raw_price_and_fundamental(ticker)
            raw = stock.get("price", "")
    else:
        from app.services.foreign_analysis import get_foreign_stock_and_fundamental
        stock, _ = await asyncio.to_thread(get_foreign_stock_and_fundamental, ticker)
        raw = stock.get("price") or stock.get("current_price", "")

    if not raw:
        raise ValueError("현재가를 가져오지 못해 거래할 수 없습니다.")
    price = float(str(raw).replace(",", ""))
    if price <= 0:
        raise ValueError("현재가를 가져오지 못해 거래할 수 없습니다.")
    _price_cache[cache_key] = (price, now)
    return price


async def _safe_price(pos) -> tuple[float, bool]:
    """(price, is_stale). is_stale=True 이면 API 실패로 avg_price 대체."""
    try:
        return await _fetch_price(pos["market"], pos["ticker"]), False
    except Exception:
        return pos["avg_price"], True


# ── 계좌 요약 ──────────────────────────────────────────────────
async def get_account_summary() -> dict:
    with _conn() as con:
        account = _ensure_account(con)
        positions = con.execute("SELECT * FROM paper_positions").fetchall()

    rate = await _get_usd_krw_rate() if any(p["market"] == "US" for p in positions) else 1.0

    price_results = await asyncio.gather(*[_safe_price(p) for p in positions])
    prices = [r[0] for r in price_results]
    stock_value_krw = round(sum(
        _to_krw(price * pos["quantity"], pos["market"], rate)
        for price, pos in zip(prices, positions)
    ))
    cash = account["cash"]
    initial = account["initial_cash"]
    deposited = account["deposited"] if "deposited" in account.keys() else 0.0
    total = round(cash + stock_value_krw)
    # 추가 자금(deposited)은 수익률 계산에서 제외 — 주식 운용 성과만 반영
    pl = round(total - initial - deposited)
    pct = round(pl / initial * 100, 2) if initial else 0

    result = {
        "initial_cash": initial,
        "cash": cash,
        "deposited": deposited,
        "stock_value": stock_value_krw,
        "total_asset": total,
        "total_profit_loss": pl,
        "total_profit_rate": pct,
    }
    if any(p["market"] == "US" for p in positions):
        result["usd_krw_rate"] = round(rate)
    return result


# ── 자금 추가 ─────────────────────────────────────────────────
def add_funds(amount: float) -> dict:
    if amount <= 0:
        raise ValueError("추가 자금은 0원보다 커야 합니다.")
    with _conn() as con:
        account = _ensure_account(con)
        cur_deposited = account["deposited"] if "deposited" in account.keys() else 0.0
        new_cash      = round(account["cash"] + amount)
        new_deposited = round(cur_deposited + amount)
        # initial_cash는 절대 변경하지 않음 — 수익률은 최초 투자금 기준으로만 계산
        con.execute(
            "UPDATE paper_account SET cash=?, deposited=? WHERE id=1",
            (new_cash, new_deposited),
        )
        con.execute(
            """INSERT INTO paper_trades
               (created_at, trade_type, market, ticker, name, price, quantity,
                amount, amount_krw, usd_krw_rate, realized_profit, realized_profit_rate)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "DEPOSIT",
             "KR", "-", "자금 추가", amount, 1,
             amount, amount, 1, None, None),
        )
    return {
        "message": f"{amount:,.0f}원이 추가되었습니다. 현재 현금: {new_cash:,.0f}원",
        "cash": new_cash,
    }


# ── 계좌 생성 / 리셋 ──────────────────────────────────────────
def create_or_reset(initial_cash: float) -> dict:
    if initial_cash <= 0:
        raise ValueError("초기 자금은 0원보다 커야 합니다.")
    with _conn() as con:
        con.execute("DELETE FROM paper_positions")
        con.execute("DELETE FROM paper_trades")
        con.execute(
            "INSERT OR REPLACE INTO paper_account (id, initial_cash, cash, deposited) VALUES (1, ?, ?, 0)",
            (initial_cash, initial_cash),
        )
    return {"message": f"가상 계좌가 {initial_cash:,.0f}원으로 초기화됐습니다."}


# ── 가상 매수 ─────────────────────────────────────────────────
async def buy(market: str, ticker: str, name: str, quantity: int) -> dict:
    if quantity < 1:
        raise ValueError("수량은 1주 이상이어야 합니다.")

    price = await _fetch_price(market, ticker)
    rate = await _get_usd_krw_rate() if market == "US" else 1.0
    amount_native = round(price * quantity, 4)
    amount_krw = _to_krw(amount_native, market, rate)

    with _conn() as con:
        account = _ensure_account(con)
        if amount_krw > account["cash"]:
            if market == "US":
                amount_str = f"${amount_native:,.2f} (약 {amount_krw:,.0f}원)"
            else:
                amount_str = f"{amount_krw:,.0f}원"
            raise ValueError(
                f"보유 현금({account['cash']:,.0f}원)이 부족합니다. "
                f"필요 금액: {amount_str}"
            )

        existing = con.execute(
            "SELECT * FROM paper_positions WHERE market=? AND ticker=?", (market, ticker)
        ).fetchone()

        if existing:
            new_qty = existing["quantity"] + quantity
            new_avg = round(
                (existing["avg_price"] * existing["quantity"] + price * quantity) / new_qty, 4
            )
            existing_cost_krw = existing["avg_cost_krw"] if "avg_cost_krw" in existing.keys() else 0
            new_avg_cost_krw = (existing_cost_krw * existing["quantity"] + amount_krw) / new_qty
            con.execute(
                "UPDATE paper_positions SET avg_price=?, quantity=?, name=?, avg_cost_krw=? WHERE market=? AND ticker=?",
                (new_avg, new_qty, name or existing["name"], new_avg_cost_krw, market, ticker),
            )
            avg_price, qty_after = new_avg, new_qty
        else:
            avg_cost_krw_per_share = amount_krw / quantity
            con.execute(
                "INSERT INTO paper_positions (market, ticker, name, avg_price, quantity, avg_cost_krw) VALUES (?,?,?,?,?,?)",
                (market, ticker, name, price, quantity, avg_cost_krw_per_share),
            )
            avg_price, qty_after = price, quantity

        new_cash = round(account["cash"] - amount_krw)
        con.execute("UPDATE paper_account SET cash=? WHERE id=1", (new_cash,))

        con.execute(
            """INSERT INTO paper_trades
               (created_at, trade_type, market, ticker, name, price, quantity,
                amount, amount_krw, usd_krw_rate, realized_profit, realized_profit_rate)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "BUY",
             market, ticker, name, price, quantity,
             amount_native, amount_krw, rate, None, None),
        )

    price_str = f"${price:,.2f} (약 {amount_krw:,.0f}원)" if market == "US" else f"{price:,.0f}원"
    return {
        "success": True,
        "message": f"{name} {quantity}주를 {price_str} 기준으로 가상 매수했습니다.",
        "cash": new_cash,
        "position": {"ticker": ticker, "name": name, "avg_price": avg_price, "quantity": qty_after},
        "usd_krw_rate": round(rate) if market == "US" else None,
    }


# ── 가상 매도 ─────────────────────────────────────────────────
async def sell(market: str, ticker: str, quantity: int) -> dict:
    if quantity < 1:
        raise ValueError("수량은 1주 이상이어야 합니다.")

    price = await _fetch_price(market, ticker)
    rate = await _get_usd_krw_rate() if market == "US" else 1.0
    amount_native = round(price * quantity, 4)
    amount_krw = _to_krw(amount_native, market, rate)

    with _conn() as con:
        account = _ensure_account(con)
        pos = con.execute(
            "SELECT * FROM paper_positions WHERE market=? AND ticker=?", (market, ticker)
        ).fetchone()

        if not pos:
            raise ValueError("보유하지 않은 종목입니다.")
        if quantity > pos["quantity"]:
            raise ValueError(f"보유 수량({pos['quantity']}주)보다 많이 매도할 수 없습니다.")

        # 실현손익: 매수 시점 원화 비용 기준 (환율 손익 포함)
        cost_per_share_krw = pos["avg_cost_krw"] if "avg_cost_krw" in pos.keys() and pos["avg_cost_krw"] else None
        if cost_per_share_krw:
            realized_krw = round(amount_krw - cost_per_share_krw * quantity)
            realized_rate = round(realized_krw / (cost_per_share_krw * quantity) * 100, 2) if cost_per_share_krw else 0
        else:
            realized_native = round((price - pos["avg_price"]) * quantity, 4)
            realized_krw = _to_krw(realized_native, market, rate)
            realized_rate = (
                round((price - pos["avg_price"]) / pos["avg_price"] * 100, 2)
                if pos["avg_price"] else 0
            )
        pos_name = pos["name"]

        remaining = pos["quantity"] - quantity
        if remaining == 0:
            con.execute("DELETE FROM paper_positions WHERE market=? AND ticker=?", (market, ticker))
        else:
            con.execute(
                "UPDATE paper_positions SET quantity=? WHERE market=? AND ticker=?",
                (remaining, market, ticker),
            )

        new_cash = round(account["cash"] + amount_krw)
        con.execute("UPDATE paper_account SET cash=? WHERE id=1", (new_cash,))

        con.execute(
            """INSERT INTO paper_trades
               (created_at, trade_type, market, ticker, name, price, quantity,
                amount, amount_krw, usd_krw_rate, realized_profit, realized_profit_rate)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "SELL",
             market, ticker, pos_name, price, quantity,
             amount_native, amount_krw, rate, realized_krw, realized_rate),
        )

    price_str = f"${price:,.2f}" if market == "US" else f"{price:,.0f}원"
    return {
        "success": True,
        "message": f"{pos_name} {quantity}주를 {price_str} 기준으로 가상 매도했습니다.",
        "cash": new_cash,
        "realized_profit": realized_krw,
        "realized_profit_rate": realized_rate,
        "usd_krw_rate": round(rate) if market == "US" else None,
    }


# ── 보유 종목 조회 ────────────────────────────────────────────
async def get_positions() -> list[dict]:
    with _conn() as con:
        positions = con.execute("SELECT * FROM paper_positions").fetchall()
        account = _ensure_account(con)

    if not positions:
        return []

    has_us = any(p["market"] == "US" for p in positions)
    rate = await _get_usd_krw_rate() if has_us else 1.0

    price_results = await asyncio.gather(*[_safe_price(p) for p in positions])

    total_asset = account["cash"]
    rows = []
    for (price, stale), pos in zip(price_results, positions):
        evalu_krw = _to_krw(price * pos["quantity"], pos["market"], rate)
        stored_cost_krw = (pos["avg_cost_krw"] if "avg_cost_krw" in pos.keys() else 0) * pos["quantity"]
        cost_krw = round(stored_cost_krw) if stored_cost_krw else _to_krw(pos["avg_price"] * pos["quantity"], pos["market"], rate)
        pl_krw    = evalu_krw - cost_krw
        pl_rate   = round(pl_krw / cost_krw * 100, 2) if cost_krw else 0
        total_asset += evalu_krw
        rows.append((pos, price, stale, cost_krw, evalu_krw, pl_krw, pl_rate))

    result = []
    for pos, price, stale, cost_krw, evalu_krw, pl_krw, pl_rate in rows:
        weight = evalu_krw / total_asset * 100 if total_asset else 0
        entry = {
            "market": pos["market"],
            "ticker": pos["ticker"],
            "name": pos["name"],
            "avg_price": pos["avg_price"],
            "current_price": price,
            "quantity": pos["quantity"],
            "cost_amount": cost_krw,
            "evaluation_amount": evalu_krw,
            "profit_loss": pl_krw,
            "profit_rate": pl_rate,
            "weight": round(weight, 1),
            "price_stale": stale,
        }
        if pos["market"] == "US":
            entry["usd_krw_rate"] = round(rate)
        result.append(entry)
    return result


# ── 거래 내역 조회 ────────────────────────────────────────────
def get_trades() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC"
        ).fetchall()
    return [
        {
            "created_at": r["created_at"],
            "trade_type": r["trade_type"],
            "market": r["market"],
            "ticker": r["ticker"],
            "name": r["name"],
            "price": r["price"],
            "quantity": r["quantity"],
            "amount": r["amount"],
            "amount_krw": r["amount_krw"],
            "usd_krw_rate": r["usd_krw_rate"],
            "realized_profit": r["realized_profit"],
            "realized_profit_rate": r["realized_profit_rate"],
        }
        for r in rows
    ]
