from fastapi import APIRouter, HTTPException
from app.schemas.recommend import RecommendRequest, RecommendResponse
from app.services.recommender.scorer import UserInput
from app.services.recommender.pipeline import recommend_stocks

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendResponse)
async def recommend(req: RecommendRequest):
    user = UserInput(
        amount=req.amount,
        risk=req.risk,
        period=req.period,
        market=req.market,
        recommend_count=req.recommend_count,
        prefer_sectors=req.prefer_sectors,
        prefer_styles=req.prefer_styles,
        exclude_sectors=req.exclude_sectors,
        exclude_tags=req.exclude_tags,
        holdings=req.holdings,
    )
    return await recommend_stocks(user)
