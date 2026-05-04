"use client";

import React, { useState, useRef, useEffect } from "react";
import {
  searchStocks, judgeHolding,
  HoldingJudgeResult, HoldingHorizon, HoldingBuyReason, SearchResult,
} from "@/lib/api";

// ── 판단 결과 배지 색상 ───────────────────────────────────────
const DECISION_STYLE: Record<string, string> = {
  "계속 보유 가능":      "bg-emerald-900 text-emerald-300 border border-emerald-700",
  "조건부 보유":         "bg-blue-900 text-blue-300 border border-blue-700",
  "일부 비중 축소 고려": "bg-yellow-900 text-yellow-300 border border-yellow-700",
  "정리 고려":           "bg-red-900 text-red-300 border border-red-700",
};

function SectionBox({ title, items, color = "gray" }: {
  title: string; items: string[]; color?: "emerald" | "blue" | "yellow" | "red" | "gray";
}) {
  const titleColors = {
    emerald: "text-emerald-400",
    blue:    "text-blue-400",
    yellow:  "text-yellow-400",
    red:     "text-red-400",
    gray:    "text-gray-300",
  };
  return (
    <div>
      <p className={`text-sm font-semibold mb-2 ${titleColors[color]}`}>{title}</p>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-sm text-gray-300 flex gap-2">
            <span className="text-gray-500 shrink-0">•</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function NumberedList({ title, items, color = "gray" }: {
  title: string; items: string[]; color?: "emerald" | "blue" | "yellow" | "red" | "gray";
}) {
  const titleColors = {
    emerald: "text-emerald-400", blue: "text-blue-400",
    yellow: "text-yellow-400",   red:  "text-red-400", gray: "text-gray-300",
  };
  return (
    <div>
      <p className={`text-sm font-semibold mb-2 ${titleColors[color]}`}>{title}</p>
      <ol className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-sm text-gray-300 flex gap-2">
            <span className="text-gray-500 shrink-0">{i + 1}.</span>
            <span>{item}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

interface HoldingsPanelProps {
  prefill?: { ticker: string; name: string; market: "domestic" | "foreign"; avgPrice: number; quantity: number } | null;
  onClearPrefill?: () => void;
}

export default function HoldingsPanel({ prefill, onClearPrefill }: HoldingsPanelProps = {}) {
  const [market, setMarket]         = useState<"domestic" | "foreign">("domestic");
  const [query, setQuery]           = useState("");
  const [ticker, setTicker]         = useState("");
  const [tickerName, setTickerName] = useState("");
  const [suggestions, setSuggestions] = useState<SearchResult[]>([]);
  const [showSugg, setShowSugg]     = useState(false);

  const [avgBuyPrice, setAvgBuyPrice] = useState("");
  const [quantity, setQuantity]       = useState("");
  const [horizon, setHorizon]         = useState<HoldingHorizon>("medium");
  const [buyReason, setBuyReason]     = useState<HoldingBuyReason>("other");

  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const [result, setResult]     = useState<HoldingJudgeResult | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 모의투자에서 prefill 수신
  useEffect(() => {
    if (!prefill) return;
    setMarket(prefill.market);
    setTicker(prefill.ticker);
    setTickerName(prefill.name);
    setQuery(prefill.name);
    setAvgBuyPrice(String(Math.round(prefill.avgPrice)));
    setQuantity(String(prefill.quantity));
    onClearPrefill?.();
  }, [prefill, onClearPrefill]);

  useEffect(() => {
    if (!query.trim()) { setSuggestions([]); return; }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await searchStocks(query, market);
        setSuggestions(res);
        setShowSugg(true);
      } catch { setSuggestions([]); }
    }, 300);
  }, [query, market]);

  function selectStock(s: SearchResult) {
    setTicker(s.ticker);
    setTickerName(s.name);
    setQuery(s.name);
    setShowSugg(false);
    setSuggestions([]);
  }

  async function handleSubmit() {
    const price = parseInt(avgBuyPrice.replace(/,/g, ""), 10);
    const qty   = parseInt(quantity.replace(/,/g, ""), 10);

    if (!ticker) { setError("종목을 선택해 주세요."); return; }
    if (!price || price <= 0) { setError("평균 매수가를 올바르게 입력해 주세요."); return; }
    if (!qty || qty <= 0)     { setError("보유 수량을 올바르게 입력해 주세요."); return; }

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const res = await judgeHolding({
        ticker, avg_buy_price: price, quantity: qty,
        horizon, buy_reason: buyReason, market,
      });
      setResult(res);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "보유 판단 중 오류가 발생했습니다.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  const pr = result?.profit_rate ?? 0;
  const profitColor = pr >= 0 ? "text-red-400" : "text-blue-400";
  const profitSign  = pr >= 0 ? "+" : "";

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* 안내 */}
      <p className="text-sm text-gray-400 text-center">
        보유 종목의 평균 매수가와 수량을 입력하면, 현재 상황에서 어떻게 판단하면 좋을지 알려드립니다.
      </p>

      {/* 입력 폼 */}
      <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800 space-y-4">
        {/* 시장 선택 */}
        <div className="flex gap-3">
          {(["domestic", "foreign"] as const).map((m) => (
            <button key={m}
              onClick={() => { setMarket(m); setQuery(""); setTicker(""); setTickerName(""); }}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition ${
                market === m ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:text-white"
              }`}>
              {m === "domestic" ? "국내" : "해외"}
            </button>
          ))}
        </div>

        {/* 종목 검색 */}
        <div className="relative">
          <label className="text-xs text-gray-400 mb-1 block">종목명 또는 코드</label>
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); setTicker(""); setTickerName(""); }}
            onFocus={() => suggestions.length > 0 && setShowSugg(true)}
            onBlur={() => setTimeout(() => setShowSugg(false), 150)}
            placeholder={market === "domestic" ? "예: NAVER, 035420" : "예: AAPL"}
            className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500"
          />
          {showSugg && suggestions.length > 0 && (
            <ul className="absolute z-10 w-full bg-gray-800 border border-gray-600 rounded-lg mt-1 max-h-48 overflow-y-auto">
              {suggestions.map((s) => (
                <li key={s.ticker} onMouseDown={() => selectStock(s)}
                  className="px-4 py-2 hover:bg-gray-700 cursor-pointer flex justify-between text-sm">
                  <span>{s.name}</span>
                  <span className="text-gray-400">{s.ticker}</span>
                </li>
              ))}
            </ul>
          )}
          {tickerName && (
            <p className="text-xs text-blue-400 mt-1">{tickerName} ({ticker}) 선택됨</p>
          )}
        </div>

        {/* 숫자 입력 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">
              평균 매수가 ({market === "domestic" ? "원" : "USD"})
            </label>
            <input
              type="text"
              value={avgBuyPrice}
              onChange={(e) => setAvgBuyPrice(e.target.value.replace(/[^0-9,]/g, ""))}
              placeholder={market === "domestic" ? "예: 210000" : "예: 180"}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">보유 수량 (주)</label>
            <input
              type="text"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value.replace(/[^0-9]/g, ""))}
              placeholder="예: 5"
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {/* 선택 옵션 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">투자 기간</label>
            <select value={horizon} onChange={(e) => setHorizon(e.target.value as HoldingHorizon)}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm">
              <option value="short">단기 (3개월 이내)</option>
              <option value="medium">중기 (3~6개월)</option>
              <option value="long">장기 (1년 이상)</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">매수 이유</label>
            <select value={buyReason} onChange={(e) => setBuyReason(e.target.value as HoldingBuyReason)}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm">
              <option value="growth">성장 기대</option>
              <option value="rebound">반등 기대</option>
              <option value="turnaround">실적 회복</option>
              <option value="long">장기 보유</option>
              <option value="value">저평가</option>
              <option value="other">기타</option>
            </select>
          </div>
        </div>

        {/* 제출 */}
        <button onClick={handleSubmit} disabled={loading}
          className="w-full bg-gradient-to-r from-indigo-600 to-blue-600 hover:opacity-90 disabled:opacity-50 py-3 rounded-xl font-semibold transition">
          {loading ? "⏳ 보유 판단 분석 중..." : "📋 보유 판단 리포트 생성"}
        </button>

        {error && <p className="text-red-400 text-sm text-center">{error}</p>}
      </div>

      {/* 결과 */}
      {result && (
        <div className="space-y-4">
          {/* 포지션 카드 */}
          <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800">
            <div className="flex justify-between items-start mb-4">
              <div>
                <p className="text-gray-400 text-xs">{result.ticker}</p>
                <h2 className="text-xl font-bold">{result.name}</h2>
              </div>
              <div className={`text-2xl font-bold ${profitColor}`}>
                {profitSign}{pr.toFixed(2)}%
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm mb-4">
              {[
                { label: "평균 매수가", value: `${result.avg_buy_price.toLocaleString()}원` },
                { label: "현재가",      value: `${result.current_price.toLocaleString()}원` },
                { label: "보유 수량",   value: `${result.quantity.toLocaleString()}주` },
                { label: "투자 원금",   value: `${result.invested_amount.toLocaleString()}원` },
                { label: "평가 금액",   value: `${result.evaluation_amount.toLocaleString()}원` },
                {
                  label: "평가 손익",
                  value: `${result.profit_loss >= 0 ? "+" : ""}${result.profit_loss.toLocaleString()}원`,
                },
              ].map(({ label, value }) => (
                <div key={label} className="bg-gray-800 rounded-lg p-3">
                  <p className="text-gray-400 text-xs mb-1">{label}</p>
                  <p className="font-medium">{value}</p>
                </div>
              ))}
            </div>
            <p className="text-sm text-gray-300">{result.position_summary}</p>
          </div>

          {/* 판단 결과 배지 */}
          <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800 text-center">
            <p className="text-xs text-gray-500 mb-2">보유 판단</p>
            <span className={`inline-block px-6 py-2 rounded-full text-lg font-bold ${DECISION_STYLE[result.decision] ?? "bg-gray-800 text-white"}`}>
              {result.decision}
            </span>
          </div>

          {/* 경고 */}
          {result.warnings.length > 0 && (
            <div className="bg-yellow-950 rounded-2xl p-4 border border-yellow-800">
              <p className="text-xs text-yellow-400 font-semibold mb-2">⚠️ 주의사항</p>
              <ul className="space-y-1">
                {result.warnings.map((w, i) => (
                  <li key={i} className="text-sm text-yellow-200">{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* 조건 블록 */}
          <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800 space-y-5">
            <SectionBox title="✅ 계속 보유해도 되는 조건" items={result.hold_conditions}   color="emerald" />
            <SectionBox title="📉 비중을 줄일 것을 고려할 때" items={result.reduce_conditions} color="yellow" />
            <SectionBox title="🚪 정리를 고려할 때"         items={result.exit_conditions}   color="red" />
          </div>

          {/* 먼저 확인할 것 */}
          <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800">
            <NumberedList title="📋 지금 가장 먼저 확인할 것" items={result.check_first} color="blue" />
          </div>

          {/* AI 리포트 */}
          {result.report && (
            <div className="bg-gray-900 rounded-2xl p-6 border border-indigo-800">
              <h3 className="font-semibold mb-4 text-indigo-300">AI 보유 판단 리포트</h3>
              <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
                {result.report}
              </div>
            </div>
          )}

          <p className="text-xs text-gray-600 text-center pb-4">
            이 결과는 투자 판단 보조 도구입니다. 최종 투자 결정의 책임은 투자자 본인에게 있습니다.
          </p>
        </div>
      )}
    </div>
  );
}
