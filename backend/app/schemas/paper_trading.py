from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


class AccountCreate(BaseModel):
    initial_cash: float = Field(10_000_000, gt=0)


class AddFundsRequest(BaseModel):
    amount: float = Field(..., gt=0)


class BuyRequest(BaseModel):
    market: Literal["KR", "US"]
    ticker: str
    name: str
    quantity: int = Field(..., ge=1)


class SellRequest(BaseModel):
    market: Literal["KR", "US"]
    ticker: str
    quantity: int = Field(..., ge=1)


class AccountOut(BaseModel):
    initial_cash: float
    cash: float
    deposited: float
    stock_value: float
    total_asset: float
    total_profit_loss: float
    total_profit_rate: float
    usd_krw_rate: Optional[float] = None


class PositionOut(BaseModel):
    market: str
    ticker: str
    name: str
    avg_price: float
    current_price: float
    quantity: int
    cost_amount: float
    evaluation_amount: float
    profit_loss: float
    profit_rate: float
    weight: float
    usd_krw_rate: Optional[float] = None
    price_stale: bool = False


class TradeOut(BaseModel):
    created_at: str
    trade_type: str
    market: str
    ticker: str
    name: str
    price: float
    quantity: int
    amount: float
    amount_krw: float
    usd_krw_rate: float
    realized_profit: Optional[float]
    realized_profit_rate: Optional[float]
