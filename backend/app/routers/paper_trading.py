from fastapi import APIRouter, HTTPException
from app.schemas.paper_trading import AccountCreate, AddFundsRequest, BuyRequest, SellRequest
import app.services.paper_trading as svc

router = APIRouter(prefix="/paper", tags=["paper_trading"])


@router.get("/account")
async def get_account():
    return await svc.get_account_summary()


@router.post("/account")
def create_account(body: AccountCreate):
    try:
        return svc.create_or_reset(body.initial_cash)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reset")
def reset_account(body: AccountCreate):
    try:
        return svc.create_or_reset(body.initial_cash)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/add-funds")
def add_funds(body: AddFundsRequest):
    try:
        return svc.add_funds(body.amount)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/buy")
async def buy(body: BuyRequest):
    try:
        return await svc.buy(body.market, body.ticker, body.name, body.quantity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sell")
async def sell(body: SellRequest):
    try:
        return await svc.sell(body.market, body.ticker, body.quantity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/positions")
async def get_positions():
    return await svc.get_positions()


@router.get("/trades")
def get_trades():
    return svc.get_trades()
