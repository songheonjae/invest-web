"use client";

import React, { useState, useEffect, useRef } from "react";
import { fetchDomesticStock, fetchForeignStock, searchStocks, fetchDomesticChart, fetchForeignChart, analyzeStock, StockData, SearchResult, CandleData, AnalysisResult, ConfidenceResult, TechnicalIndicators } from "@/lib/api";
import StockChart from "@/components/StockChart";
import GlossaryModal from "@/components/GlossaryModal";
import RecommendPanel from "@/components/RecommendPanel";
import HoldingsPanel from "@/components/HoldingsPanel";
import PaperTradingPanel from "@/components/PaperTradingPanel";

function TechRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between items-start py-2 border-b border-gray-800 last:border-0 text-sm">
      <span className="text-gray-400 w-28 shrink-0">{label}</span>
      <span className="text-white font-medium text-right flex-1">{children}</span>
    </div>
  );
}

function TechnicalSection({ ind }: { ind: TechnicalIndicators }) {
  const [open, setOpen] = React.useState(false);
  const rsiColor = ind.rsi == null ? "" : ind.rsi >= 70 ? "text-red-400" : ind.rsi <= 30 ? "text-blue-400" : "";
  return (
    <div>
      <button onClick={() => setOpen(v => !v)}
        className="flex items-center justify-between w-full font-semibold text-purple-300 mb-2">
        <span>기술적 지표</span>
        <span className="text-gray-400 text-sm">{open ? "▲ 접기" : "▼ 펼치기"}</span>
      </button>
      {open && <div className="bg-gray-800 rounded-xl p-4">
        <TechRow label="추세">{ind.trend ?? "N/A"}</TechRow>
        <TechRow label="RSI(14)">
          <span className={rsiColor}>{ind.rsi ?? "N/A"}</span>
          {ind.rsi_comment && <span className="text-gray-400 ml-1 text-xs">{ind.rsi_comment.split("—")[1]?.trim()}</span>}
        </TechRow>
        <TechRow label="MACD">{ind.macd_comment ?? "N/A"}</TechRow>
        <TechRow label="볼린저밴드">{ind.bb_position ?? "N/A"}</TechRow>
        <TechRow label="MA5 / MA20">{`${ind.ma5?.toLocaleString() ?? "N/A"} / ${ind.ma20?.toLocaleString() ?? "N/A"}`}</TechRow>
        <TechRow label="MA60 / MA120">{`${ind.ma60?.toLocaleString() ?? "N/A"} / ${ind.ma120?.toLocaleString() ?? "N/A"}`}</TechRow>
        {ind.cross && <TechRow label="크로스 신호">{ind.cross}</TechRow>}
        <TechRow label="거래량">{`평균 대비 ${ind.vol_ratio ?? "N/A"}배`}</TechRow>
        <TechRow label="52주 고점 대비">{ind.gap_from_high != null ? `${ind.gap_from_high}%` : "N/A"}</TechRow>
        <TechRow label="52주 저점 대비">{ind.rebound_from_low != null ? `+${ind.rebound_from_low}%` : "N/A"}</TechRow>
      </div>}
    </div>
  );
}

function ScoreCard({ label, score, max, color, extra }: { label: string; score: number; max: number; color: "blue" | "emerald"; extra?: string }) {
  const pct = Math.min(100, Math.round((score / max) * 100));
  const barColor = color === "blue" ? "bg-blue-500" : "bg-emerald-500";
  const textColor = color === "blue" ? "text-blue-400" : "text-emerald-400";
  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <div className="flex justify-between items-center mb-2">
        <span className="text-sm text-gray-400">{label}</span>
        <span className={`text-lg font-bold ${textColor}`}>{score}<span className="text-xs text-gray-500 ml-0.5">/{max}</span></span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-2">
        <div className={`${barColor} h-2 rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      {extra && <p className="text-xs text-yellow-400 mt-1">{extra}</p>}
    </div>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<"domestic" | "foreign">("domestic");
  const [data, setData] = useState<StockData | null>(null);
  const [chartData, setChartData] = useState<CandleData[]>([]);
  const [period, setPeriod] = useState("D");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [suggestions, setSuggestions] = useState<SearchResult[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [showFundamental, setShowFundamental] = useState(false);
  const [showFinancials, setShowFinancials] = useState(false);
  const [activeTab, setActiveTab] = useState<"analysis" | "holdings" | "recommend" | "paper">("analysis");
  const [holdingsPrefill, setHoldingsPrefill] = useState<{
    ticker: string; name: string; market: "domestic" | "foreign";
    avgPrice: number; quantity: number;
  } | null>(null);
  const [paperPrefill, setPaperPrefill] = useState<{
    ticker: string; name: string; market: "KR" | "US";
  } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentTickerRef = useRef("");

  useEffect(() => {
    if (!query.trim()) { setSuggestions([]); return; }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const results = await searchStocks(query, market);
        setSuggestions(results);
        setShowSuggestions(true);
      } catch { setSuggestions([]); }
    }, 300);
  }, [query, market]);

  useEffect(() => {
    if (currentTickerRef.current && market === "domestic") {
      fetchDomesticChart(currentTickerRef.current, period).then(setChartData).catch(() => {});
    }
  }, [period, market]);

  async function search(ticker?: string, name?: string) {
    const target = ticker || query.trim().toUpperCase();
    if (!target) return;
    setLoading(true);
    setError("");
    setData(null);
    setChartData([]);
    setAnalysis(null);
    setShowSuggestions(false);
    if (name) setQuery(name);
    currentTickerRef.current = target;
    try {
      const result = market === "domestic"
        ? await fetchDomesticStock(target)
        : await fetchForeignStock(target);
      setData({ ...result, name: result.name || name || target });
      if (market === "domestic") {
        fetchDomesticChart(target, period).then(setChartData).catch(() => {});
      } else {
        fetchForeignChart(target).then(setChartData).catch(() => {});
      }
    } catch {
      setError("종목을 찾을 수 없습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function runAnalysis() {
    if (!currentTickerRef.current) return;
    setAnalyzing(true);
    setAnalysis(null);
    try {
      const result = await analyzeStock(currentTickerRef.current, market);
      setAnalysis(result);
    } catch {
      setError("분석 중 오류가 발생했습니다.");
    } finally {
      setAnalyzing(false);
    }
  }

  const changeRate = data ? Number(data.change_rate) : 0;
  const isPositive = changeRate >= 0;

  return (
    <main className="min-h-screen bg-gray-950 text-white p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">📈 주식 분석</h1>
        <GlossaryModal />
      </div>

      {/* 탭 */}
      <div className="flex border-b border-gray-800 mb-8 max-w-2xl mx-auto overflow-x-auto">
        {(["analysis", "holdings", "paper", "recommend"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-5 py-2.5 text-sm font-medium transition border-b-2 -mb-px whitespace-nowrap ${
              activeTab === tab
                ? "border-blue-500 text-white"
                : "border-transparent text-gray-500 hover:text-gray-300"
            }`}
          >
            {tab === "analysis" ? "종목 분석"
              : tab === "holdings" ? "보유 판단"
              : tab === "paper" ? "모의투자"
              : "AI 투자 후보 추천"}
          </button>
        ))}
      </div>

      {/* 탭 컨텐츠 — 언마운트 없이 CSS로 숨김 (상태 유지) */}
      <div className={activeTab === "holdings" ? "" : "hidden"}>
        <HoldingsPanel
          prefill={holdingsPrefill}
          onClearPrefill={() => setHoldingsPrefill(null)}
        />
      </div>
      <div className={activeTab === "recommend" ? "" : "hidden"}>
        <RecommendPanel
          onAddToPaper={(ticker, name, market) => {
            setPaperPrefill({ ticker, name, market: market === "KR" ? "KR" : "US" });
            setActiveTab("paper");
          }}
        />
      </div>
      <div className={activeTab === "paper" ? "" : "hidden"}>
        <PaperTradingPanel
          prefill={paperPrefill}
          onClearPrefill={() => setPaperPrefill(null)}
          onJudgeHolding={(ticker, name, market, avgPrice, quantity) => {
            setHoldingsPrefill({
              ticker, name,
              market: market === "KR" ? "domestic" : "foreign",
              avgPrice, quantity,
            });
            setActiveTab("holdings");
          }}
        />
      </div>

      {activeTab === "analysis" && <>
      {/* 검색 */}
      <div className="relative flex gap-3 max-w-2xl mx-auto mb-10">
        <select
          value={market}
          onChange={(e) => { setMarket(e.target.value as "domestic" | "foreign"); setQuery(""); setSuggestions([]); setData(null); setChartData([]); setAnalysis(null); }}
          className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm"
        >
          <option value="domestic">국내</option>
          <option value="foreign">해외</option>
        </select>
        <div className="relative flex-1">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
            onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
            onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
            placeholder={market === "domestic" ? "종목명 또는 코드 (예: 삼성전자)" : "티커 (예: AAPL)"}
            className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 outline-none focus:border-blue-500"
          />
          {showSuggestions && suggestions.length > 0 && (
            <ul className="absolute z-10 w-full bg-gray-800 border border-gray-600 rounded-lg mt-1 max-h-48 overflow-y-auto">
              {suggestions.map((s) => (
                <li key={s.ticker} onMouseDown={() => search(s.ticker, s.name)}
                  className="px-4 py-2 hover:bg-gray-700 cursor-pointer flex justify-between text-sm">
                  <span>{s.name}</span>
                  <span className="text-gray-400">{s.ticker}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button onClick={() => search()} disabled={loading}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 px-5 py-2 rounded-lg font-medium transition">
          {loading ? "조회중..." : "검색"}
        </button>
      </div>

      {error && <p className="text-center text-red-400 mb-6">{error}</p>}

      {data && (
        <div className="max-w-2xl mx-auto space-y-4">
          {/* 가격 카드 */}
          <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800">
            <div className="flex justify-between items-start mb-4">
              <div>
                <p className="text-gray-400 text-sm">{data.ticker}</p>
                <h2 className="text-xl font-bold">{data.name || data.ticker}</h2>
              </div>
              <div className="text-right">
                <p className="text-3xl font-bold">
                  {market === "domestic" ? `${Number(data.price).toLocaleString()}원` : `$${Number(data.price).toLocaleString()}`}
                </p>
                <p className={`text-lg font-medium ${isPositive ? "text-red-400" : "text-blue-400"}`}>
                  {isPositive ? "▲" : "▼"} {Math.abs(changeRate).toFixed(2)}%
                </p>
              </div>
            </div>
            <div className="grid grid-cols-4 gap-3 text-sm">
              {[
                { label: "거래량", value: Number(data.volume).toLocaleString() },
                { label: "고가", value: Number(data.high).toLocaleString() },
                { label: "저가", value: Number(data.low).toLocaleString() },
                { label: "시가", value: Number(data.open).toLocaleString() },
              ].map(({ label, value }) => (
                <div key={label} className="bg-gray-800 rounded-lg p-3">
                  <p className="text-gray-400 text-xs mb-1">{label}</p>
                  <p className="font-medium">{value}</p>
                </div>
              ))}
            </div>
          </div>

          {/* 차트 */}
          <div className="bg-gray-900 rounded-2xl p-6 border border-gray-800">
            <div className="flex justify-between items-center mb-4">
              <h3 className="font-semibold">차트</h3>
              {market === "domestic" && (
                <div className="flex gap-2 text-sm">
                  {[["D", "일"], ["W", "주"], ["M", "월"]].map(([val, label]) => (
                    <button key={val} onClick={() => setPeriod(val)}
                      className={`px-3 py-1 rounded-lg transition ${period === val ? "bg-blue-600" : "bg-gray-800 hover:bg-gray-700"}`}>
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {chartData.length > 0
              ? <StockChart data={chartData} />
              : <p className="text-center text-gray-500 py-10">차트 데이터 로딩중...</p>}
          </div>

          {/* AI 분석 버튼 */}
          <button onClick={runAnalysis} disabled={analyzing}
              className="w-full bg-gradient-to-r from-purple-600 to-blue-600 hover:opacity-90 disabled:opacity-50 py-3 rounded-2xl font-semibold text-lg transition">
              {analyzing ? `🤖 ${data?.name || query} 분석 중...` : "🤖 AI 투자 분석 리포트 생성"}
            </button>

          {/* 분석 결과 */}
          {analysis && (
            <div className="bg-gray-900 rounded-2xl p-6 border border-purple-800 space-y-6">
              <h2 className="text-lg font-bold text-white">{data?.name || query} 분석</h2>
              {/* 재무지표 */}
              <div>
                <button onClick={() => setShowFundamental(v => !v)}
                  className="flex items-center justify-between w-full font-semibold text-purple-300 mb-2">
                  <span>재무지표</span>
                  <span className="text-gray-400 text-sm">{showFundamental ? "▲ 접기" : "▼ 펼치기"}</span>
                </button>
                {showFundamental && (
                  <div className="grid grid-cols-3 gap-3 text-sm">
                    {[
                      { label: "PER", value: analysis.fundamental.per ? `${analysis.fundamental.per}배` : "N/A" },
                      { label: "PBR", value: analysis.fundamental.pbr ? `${analysis.fundamental.pbr}배` : "N/A" },
                      { label: "시가총액", value: analysis.fundamental.market_cap ? (market === "foreign" ? (() => { const n = Number(analysis.fundamental.market_cap); return n >= 1e12 ? `$${(n/1e12).toFixed(1)}T` : n >= 1e9 ? `$${(n/1e9).toFixed(1)}B` : `$${(n/1e6).toFixed(1)}M`; })() : `${Number(analysis.fundamental.market_cap).toLocaleString()}억`) : "N/A" },
                      { label: "52주 최고", value: analysis.fundamental.week52_high ? (market === "foreign" ? `$${Number(analysis.fundamental.week52_high).toFixed(2)}` : `${Number(analysis.fundamental.week52_high).toLocaleString()}원`) : "N/A" },
                      { label: "52주 최저", value: analysis.fundamental.week52_low ? (market === "foreign" ? `$${Number(analysis.fundamental.week52_low).toFixed(2)}` : `${Number(analysis.fundamental.week52_low).toLocaleString()}원`) : "N/A" },
                      { label: "외국인비율", value: analysis.fundamental.foreign_rate ? `${analysis.fundamental.foreign_rate}%` : "N/A" },
                    ].map(({ label, value }) => (
                      <div key={label} className="bg-gray-800 rounded-lg p-3">
                        <p className="text-gray-400 text-xs mb-1">{label}</p>
                        <p className="font-medium">{value}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 실적 데이터 */}
              {analysis.financials && (
                <div>
                  <button onClick={() => setShowFinancials(v => !v)}
                    className="flex items-center justify-between w-full font-semibold text-purple-300 mb-2">
                    <span>실적 데이터 ({analysis.financials?.unit ?? (market === "foreign" ? "USD M" : "억원")})</span>
                    <span className="text-gray-400 text-sm">{showFinancials ? "▲ 접기" : "▼ 펼치기"}</span>
                  </button>
                  {showFinancials && <div className="space-y-4">
                    {analysis.financials.annual && analysis.financials.annual.length > 0 && (
                      <div>
                        <p className="text-xs text-gray-400 mb-2">연간 실적</p>
                        <div className="overflow-x-auto">
                          <table className="w-full text-xs text-center border-collapse">
                            <thead>
                              <tr className="bg-gray-800">
                                <th className="py-2 px-3 text-left text-gray-400">항목</th>
                                {analysis.financials.annual.map((r) => (
                                  <th key={r.period} className="py-2 px-3 text-gray-300">{r.period}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {["매출액", "영업이익", "당기순이익", "영업이익률", "ROE"].map((field) => (
                                <tr key={field} className="border-t border-gray-800 hover:bg-gray-800/50">
                                  <td className="py-2 px-3 text-left text-gray-400">{field}</td>
                                  {analysis.financials!.annual.map((r) => (
                                    <td key={r.period} className="py-2 px-3 text-gray-200">
                                      {r[field] ?? "N/A"}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                    {analysis.financials.quarterly && analysis.financials.quarterly.length > 0 && (
                      <div>
                        <p className="text-xs text-gray-400 mb-2">분기 실적</p>
                        <div className="overflow-x-auto">
                          <table className="w-full text-xs text-center border-collapse">
                            <thead>
                              <tr className="bg-gray-800">
                                <th className="py-2 px-3 text-left text-gray-400">항목</th>
                                {analysis.financials.quarterly.map((r) => (
                                  <th key={r.period} className="py-2 px-3 text-gray-300">{r.period}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {["매출액", "영업이익", "당기순이익", "영업이익률"].map((field) => (
                                <tr key={field} className="border-t border-gray-800 hover:bg-gray-800/50">
                                  <td className="py-2 px-3 text-left text-gray-400">{field}</td>
                                  {analysis.financials!.quarterly.map((r) => (
                                    <td key={r.period} className="py-2 px-3 text-gray-200">
                                      {r[field] ?? "N/A"}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>}
                </div>
              )}

              {/* 기술적 지표 */}
              {analysis.indicators && Object.keys(analysis.indicators).length > 0 && (
                <TechnicalSection ind={analysis.indicators} />
              )}

              {/* 신뢰도 / 매력도 */}
              {analysis.confidence && (
                <div className="grid grid-cols-2 gap-3">
                  <ScoreCard label="분석 신뢰도" score={analysis.confidence.reliability.score} max={80} color="blue" />
                  <ScoreCard label="투자 매력도" score={analysis.confidence.attractiveness.score} max={100} color="emerald" extra={analysis.confidence.is_turnaround ? "턴어라운드" : undefined} />
                </div>
              )}

              {/* 리포트 */}
              <div>
                <h3 className="font-semibold mb-3 text-purple-300">AI 분석 리포트</h3>
                <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
                  {analysis.report}
                </div>
              </div>

              {/* 뉴스 */}
              {analysis.news.length > 0 && (
                <div>
                  <h3 className="font-semibold mb-3 text-purple-300">최근 뉴스</h3>
                  <ul className="space-y-2">
                    {analysis.news.map((n, i) => (
                      <li key={i} className="text-sm">
                        <a href={n.url} target="_blank" rel="noopener noreferrer"
                          className="text-blue-400 hover:underline">{n.title}</a>
                        <span className="text-gray-500 ml-2">{n.date} · {n.source}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      </>}
    </main>
  );
}
