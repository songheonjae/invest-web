from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

router = APIRouter(prefix="/holdings", tags=["holdings"])


class HoldingJudgeRequest(BaseModel):
    ticker:        str
    avg_buy_price: int = Field(gt=0, description="평균 매수가 (원)")
    quantity:      int = Field(gt=0, description="보유 수량")
    horizon:       Literal["short", "medium", "long"] = "medium"
    buy_reason:    Literal["rebound", "turnaround", "long", "value", "growth", "other"] = "other"
    market:        Literal["domestic", "foreign"] = "domestic"


@router.post("/judge")
async def judge_holding_api(req: HoldingJudgeRequest):
    from app.services.holdings import judge_holding
    try:
        result = await judge_holding(
            ticker=req.ticker,
            avg_buy_price=req.avg_buy_price,
            quantity=req.quantity,
            horizon=req.horizon,
            buy_reason=req.buy_reason,
            market=req.market,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
