import json
import logging
import os
from datetime import datetime, timezone

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "analysis.jsonl")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("invest")


def _ensure_log_dir():
    os.makedirs(_LOG_DIR, exist_ok=True)


def log_analysis(
    ticker: str,
    market: str,  # "domestic" | "foreign"
    *,
    collection: dict,   # {price, fundamental, financials, chart, news}
    validation: dict | None,
    news_count: int,
    ai_success: bool,
    confidence: dict | None,
    error: str | None = None,
    needs_review: bool = False,
    review_reason: str | None = None,
):
    """분석 요청 1건을 logs/analysis.jsonl 에 기록한다."""
    _ensure_log_dir()

    val_summary = None
    if validation:
        val_summary = {
            "max_severity": validation.get("max_severity", "ok"),
            "severe": sum(1 for i in validation.get("issues", []) if i["severity"] == "severe"),
            "medium": sum(1 for i in validation.get("issues", []) if i["severity"] == "medium"),
            "light":  sum(1 for i in validation.get("issues", []) if i["severity"] == "light"),
        }

    conf_summary = None
    if confidence:
        rel = confidence.get("reliability", {})
        att = confidence.get("attractiveness", {})
        conf_summary = {
            "total": confidence.get("total"),
            "reliability": rel.get("score"),
            "reliability_label": rel.get("label"),
            "attractiveness": att.get("score"),
            "attractiveness_label": att.get("label"),
            "is_turnaround": confidence.get("is_turnaround", False),
        }

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "market": market,
        "collection": collection,
        "validation": val_summary,
        "news_count": news_count,
        "ai_success": ai_success,
        "confidence": conf_summary,
        "error": error,
        "needs_review": needs_review,
        "review_reason": review_reason,
    }

    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("로그 파일 쓰기 실패: %s", e)

    # 콘솔에도 요약 출력
    status = "✓" if ai_success else "✗"
    val_str = f"val={val_summary['max_severity']}" if val_summary else "val=skip"
    conf_str = f"rel={conf_summary['reliability']} att={conf_summary['attractiveness']}" if conf_summary else ""
    flag = " ⚑ REVIEW" if needs_review else ""
    logger.info("[%s] %s %s news=%d %s %s%s",
                market, status, ticker, news_count, val_str, conf_str, flag)


def flag_review(ticker: str, reason: str):
    """특정 종목에 수동 검토 필요 메모를 남긴다 (별도 라인)."""
    _ensure_log_dir()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "manual_flag",
        "ticker": ticker,
        "reason": reason,
    }
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
