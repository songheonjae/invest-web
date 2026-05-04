from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import stock
from app.routers import recommend
from app.routers import holdings
from app.routers import paper_trading

app = FastAPI(title="Invest Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stock.router)
app.include_router(recommend.router)
app.include_router(holdings.router)
app.include_router(paper_trading.router)

@app.get("/")
def root():
    return {"status": "ok"}
