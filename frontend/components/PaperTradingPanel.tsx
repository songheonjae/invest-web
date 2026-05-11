"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  searchStocks, SearchResult,
  fetchDomesticStock, fetchForeignStock,
  paperGetAccount, paperResetAccount, paperAddFunds,
  paperBuy, paperSell,
  paperGetPositions, paperGetTrades,
  PaperAccount, PaperPosition, PaperTrade,
} from "@/lib/api";

interface Props {
  prefill?: { ticker: string; name: string; market: "KR" | "US" } | null;
  onClearPrefill?: () => void;
  onJudgeHolding?: (ticker: string, name: string, market: string, avgPrice: number, quantity: number) => void;
}

const fmt = (n: number, isForeign = false) =>
  isForeign ? `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : `${Math.round(n).toLocaleString()}원`;

const pct = (n: number) => {
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
};

export default function PaperTradingPanel({ prefill, onClearPrefill, onJudgeHolding }: Props) {
  const [account, setAccount]       = useState<PaperAccount | null>(null);
  const [positions, setPositions]   = useState<PaperPosition[]>([]);
  const [trades, setTrades]         = useState<PaperTrade[]>([]);
  const [loading, setLoading]       = useState(false);
  const [msg, setMsg]               = useState<{ text: string; ok: boolean } | null>(null);

  // 계좌 설정
  const [initCash, setInitCash]     = useState("10000000");
  const [showReset, setShowReset]   = useState(false);
  const [showAddFunds, setShowAddFunds] = useState(false);
  const [addAmount, setAddAmount]   = useState("1000000");

  // 매수/매도 폼
  const [market, setMarket]         = useState<"KR" | "US">("KR");
  const [query, setQuery]           = useState("");
  const [ticker, setTicker]         = useState("");
  const [stockName, setStockName]   = useState("");
  const [quantity, setQuantity]     = useState("1");
  const [suggestions, setSuggestions] = useState<SearchResult[]>([]);
  const [showSugg, setShowSugg]     = useState(false);
  const [currentPrice, setCurrentPrice] = useState<number | null>(null);
  const [priceLoading, setPriceLoading] = useState(false);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 탭
  const [subTab, setSubTab] = useState<"trade" | "positions" | "history">("trade");

  const load = useCallback(async () => {
    try {
      const [acc, pos, tr] = await Promise.all([
        paperGetAccount(), paperGetPositions(), paperGetTrades(),
      ]);
      setAccount(acc);
      setPositions(pos);
      setTrades(tr);
    } catch {
      setMsg({ text: "서버에서 데이터를 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인해 주세요.", ok: false });
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { load(); }, [subTab, load]);

  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  // 추천/보유 판단에서 prefill 수신
  useEffect(() => {
    if (!prefill) return;
    setMarket(prefill.market);
    setQuery(prefill.name);
    setTicker(prefill.ticker);
    setStockName(prefill.name);
    setSubTab("trade");
    onClearPrefill?.();
  }, [prefill, onClearPrefill]);

  // 종목 자동완성
  useEffect(() => {
    if (!query.trim()) { setSuggestions([]); return; }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await searchStocks(query, market === "KR" ? "domestic" : "foreign");
        setSuggestions(r);
        setShowSugg(r.length > 0);
      } catch { setSuggestions([]); }
    }, 300);
  }, [query, market]);

  function selectStock(s: SearchResult) {
    setTicker(s.ticker);
    setStockName(s.name);
    setQuery(s.name);
    setShowSugg(false);
    setCurrentPrice(null);
  }

  async function lookupPrice() {
    const t = ticker || query.trim().toUpperCase();
    if (!t) return;
    if (!ticker) { setTicker(t); setStockName(t); }
    setPriceLoading(true);
    setCurrentPrice(null);
    setMsg(null);
    try {
      const d = market === "KR"
        ? await fetchDomesticStock(t)
        : await fetchForeignStock(t);
      const raw = d.price ?? (d as unknown as Record<string, unknown>).current_price ?? "0";
      const p = parseFloat(String(raw).replace(/,/g, ""));
      if (p > 0) {
        setCurrentPrice(p);
        if (!stockName && d.name) setStockName(d.name);
      } else {
        setMsg({ text: "현재가를 가져오지 못했습니다.", ok: false });
      }
    } catch {
      setMsg({ text: "현재가를 가져오지 못했습니다. 종목 코드를 확인해 주세요.", ok: false });
    } finally {
      setPriceLoading(false);
    }
  }

  async function handleBuy() {
    const t = ticker || query.trim().toUpperCase();
    const qty = parseInt(quantity);
    if (!t || qty < 1) { setMsg({ text: "종목과 수량을 확인해 주세요.", ok: false }); return; }
    setLoading(true); setMsg(null);
    try {
      const res = await paperBuy(market, t, stockName || t, qty);
      setMsg({ text: res.message, ok: true });
      await load();
      setSubTab("positions");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      setMsg({ text: err?.response?.data?.detail ?? "매수 중 오류가 발생했습니다.", ok: false });
    } finally { setLoading(false); }
  }

  async function handleSell() {
    const t = ticker || query.trim().toUpperCase();
    const qty = parseInt(quantity);
    if (!t || qty < 1) { setMsg({ text: "종목과 수량을 확인해 주세요.", ok: false }); return; }
    const pos = positions.find(p => p.ticker === t && p.market === market);
    if (!pos) { setMsg({ text: "보유하지 않은 종목입니다.", ok: false }); return; }
    setLoading(true); setMsg(null);
    try {
      const res = await paperSell(market, t, qty);
      setMsg({ text: res.message, ok: true });
      await load();
      setSubTab("positions");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      setMsg({ text: err?.response?.data?.detail ?? "매도 중 오류가 발생했습니다.", ok: false });
    } finally { setLoading(false); }
  }

  async function handleReset() {
    const cash = parseFloat(initCash.replace(/,/g, ""));
    if (!cash || cash <= 0) { setMsg({ text: "초기 자금을 올바르게 입력해 주세요.", ok: false }); return; }
    setLoading(true); setMsg(null);
    try {
      const res = await paperResetAccount(cash);
      setMsg({ text: res.message, ok: true });
      setShowReset(false);
      await load();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      setMsg({ text: err?.response?.data?.detail ?? "초기화 중 오류가 발생했습니다.", ok: false });
    } finally { setLoading(false); }
  }

  // 보유 종목 탭 인라인 매도
  const [sellTarget, setSellTarget] = useState<{ market: "KR" | "US"; ticker: string; name: string; maxQty: number } | null>(null);
  const [sellQty, setSellQty]       = useState("1");
  const [sellLoading, setSellLoading] = useState(false);

  async function handleQuickSell() {
    if (!sellTarget) return;
    const qty = parseInt(sellQty);
    if (isNaN(qty) || qty < 1 || qty > sellTarget.maxQty) {
      setMsg({ text: `수량은 1~${sellTarget.maxQty}주 사이로 입력해 주세요.`, ok: false });
      return;
    }
    setSellLoading(true); setMsg(null);
    try {
      const res = await paperSell(sellTarget.market, sellTarget.ticker, qty);
      setMsg({ text: res.message, ok: true });
      setSellTarget(null);
      await load();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      setMsg({ text: err?.response?.data?.detail ?? "매도 중 오류가 발생했습니다.", ok: false });
    } finally { setSellLoading(false); }
  }

  async function handleAddFunds() {
    const amount = parseFloat(addAmount.replace(/,/g, ""));
    if (!amount || amount <= 0) { setMsg({ text: "추가할 금액을 올바르게 입력해 주세요.", ok: false }); return; }
    setLoading(true); setMsg(null);
    try {
      const res = await paperAddFunds(amount);
      setMsg({ text: res.message, ok: true });
      setShowAddFunds(false);
      await load();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      setMsg({ text: err?.response?.data?.detail ?? "자금 추가 중 오류가 발생했습니다.", ok: false });
    } finally { setLoading(false); }
  }

  const isForeign = market === "US";
  const selectedPos = positions.find(p => p.ticker === (ticker || query.trim().toUpperCase()) && p.market === market);

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      {/* 안내 문구 */}
      <div className="bg-gray-800/60 border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-400">
        모의투자는 실제 돈이 아닌 가상 자금으로 투자 연습을 해보는 기능입니다.
        현재가 기준으로 가상 매수/매도되며, 실제 체결 결과와 다를 수 있습니다.
      </div>

      {/* 계좌 요약 */}
      {account && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <div className="flex justify-between items-center mb-4">
            <h2 className="font-bold text-lg">💰 가상 계좌</h2>
            <div className="flex gap-2">
              <button onClick={() => { setShowAddFunds(v => !v); setShowReset(false); }}
                className="text-xs text-emerald-500 hover:text-emerald-300 border border-emerald-800 rounded-lg px-3 py-1">
                자금 추가
              </button>
              <button onClick={() => { setShowReset(v => !v); setShowAddFunds(false); }}
                className="text-xs text-gray-500 hover:text-gray-300 border border-gray-700 rounded-lg px-3 py-1">
                계좌 초기화
              </button>
            </div>
          </div>

          {showAddFunds && (
            <div className="mb-4 flex gap-2 items-center">
              <input
                value={addAmount}
                onChange={e => setAddAmount(e.target.value)}
                className="bg-gray-800 border border-emerald-700 rounded-lg px-3 py-1.5 text-sm w-40"
                placeholder="추가 금액 (원)"
              />
              <button onClick={handleAddFunds} disabled={loading}
                className="bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 px-4 py-1.5 rounded-lg text-sm">
                추가 확인
              </button>
              <button onClick={() => setShowAddFunds(false)}
                className="text-gray-500 hover:text-gray-300 text-sm px-2">취소</button>
            </div>
          )}

          {showReset && (
            <div className="mb-4 flex gap-2 items-center">
              <input
                value={initCash}
                onChange={e => setInitCash(e.target.value)}
                className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-1.5 text-sm w-40"
                placeholder="초기 자금 (원)"
              />
              <button onClick={handleReset} disabled={loading}
                className="bg-red-700 hover:bg-red-600 disabled:opacity-50 px-4 py-1.5 rounded-lg text-sm">
                초기화 확인
              </button>
              <button onClick={() => setShowReset(false)}
                className="text-gray-500 hover:text-gray-300 text-sm px-2">취소</button>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 text-sm">
            {[
              { label: "초기 자금",      value: fmt(account.initial_cash) },
              { label: "현재 현금",      value: fmt(account.cash) },
              { label: "주식 평가금액",  value: fmt(account.stock_value) },
            ].map(({ label, value }) => (
              <div key={label} className="bg-gray-800 rounded-xl p-3">
                <p className="text-gray-400 text-xs mb-1">{label}</p>
                <p className="font-semibold">{value}</p>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-3 text-sm mt-3">
            <div className="bg-gray-800 rounded-xl p-3">
              <p className="text-gray-400 text-xs mb-1">총 평가자산</p>
              <p className="font-bold text-white">{fmt(account.total_asset)}</p>
            </div>
            <div className="bg-gray-800 rounded-xl p-3">
              <p className="text-gray-400 text-xs mb-1">
                총 평가손익
                {account.deposited > 0 && <span className="text-gray-500 ml-1">(원금 기준)</span>}
              </p>
              <p className={`font-bold ${account.total_profit_loss >= 0 ? "text-red-400" : "text-blue-400"}`}>
                {account.total_profit_loss >= 0 ? "+" : ""}{fmt(account.total_profit_loss)}
              </p>
            </div>
            <div className="bg-gray-800 rounded-xl p-3">
              <p className="text-gray-400 text-xs mb-1">
                총 수익률
                {account.deposited > 0 && <span className="text-gray-500 ml-1">(원금 기준)</span>}
              </p>
              <p className={`font-bold ${account.total_profit_rate >= 0 ? "text-red-400" : "text-blue-400"}`}>
                {pct(account.total_profit_rate)}
              </p>
            </div>
          </div>
          {account.deposited > 0 && (
            <div className="mt-3 bg-gray-800/50 border border-emerald-900/50 rounded-xl px-3 py-2 text-xs text-gray-400 flex justify-between">
              <span>추가 자금 (수익률 계산 제외)</span>
              <span className="text-emerald-400 font-medium">{fmt(account.deposited)}</span>
            </div>
          )}
        </div>
      )}

      {/* 메시지 */}
      {msg && (
        <div className={`text-sm px-4 py-2 rounded-lg ${msg.ok ? "bg-emerald-900/50 text-emerald-300 border border-emerald-800" : "bg-red-900/50 text-red-300 border border-red-800"}`}>
          {msg.text}
        </div>
      )}

      {/* 서브탭 */}
      <div className="flex border-b border-gray-800">
        {(["trade", "positions", "history"] as const).map(t => (
          <button key={t} onClick={() => setSubTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition ${subTab === t ? "border-blue-500 text-white" : "border-transparent text-gray-500 hover:text-gray-300"}`}>
            {t === "trade" ? "매수 / 매도" : t === "positions" ? `보유 종목 (${positions.length})` : `거래 내역 (${trades.length})`}
          </button>
        ))}
      </div>

      {/* ── 매수/매도 탭 ── */}
      {subTab === "trade" && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
          {/* 시장 선택 */}
          <div className="flex gap-2">
            {(["KR", "US"] as const).map(m => (
              <button key={m} onClick={() => { setMarket(m); setQuery(""); setTicker(""); setStockName(""); setCurrentPrice(null); setSuggestions([]); }}
                className={`px-4 py-1.5 rounded-lg text-sm font-medium transition ${market === m ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:text-white"}`}>
                {m === "KR" ? "국내" : "해외"}
              </button>
            ))}
          </div>

          {/* 종목 검색 */}
          <div className="relative">
            <label className="text-xs text-gray-400 mb-1 block">종목명 또는 코드</label>
            <input
              value={query}
              onChange={e => { setQuery(e.target.value); setTicker(""); setStockName(""); setCurrentPrice(null); }}
              onFocus={() => suggestions.length > 0 && setShowSugg(true)}
              onBlur={() => setTimeout(() => setShowSugg(false), 150)}
              onKeyDown={e => e.key === "Enter" && lookupPrice()}
              placeholder={market === "KR" ? "예: 삼성전자, 005930" : "예: AAPL, TSLA"}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500"
            />
            {showSugg && suggestions.length > 0 && (
              <ul className="absolute z-10 w-full bg-gray-800 border border-gray-600 rounded-lg mt-1 max-h-48 overflow-y-auto">
                {suggestions.map(s => (
                  <li key={s.ticker} onMouseDown={() => selectStock(s)}
                    className="px-4 py-2 hover:bg-gray-700 cursor-pointer flex justify-between text-sm">
                    <span>{s.name}</span>
                    <span className="text-gray-400">{s.ticker}</span>
                  </li>
                ))}
              </ul>
            )}
            {ticker && <p className="text-xs text-blue-400 mt-1">{stockName} ({ticker}) 선택됨</p>}
          </div>

          {/* 현재가 조회 */}
          <div className="flex items-center gap-3">
            <button onClick={lookupPrice} disabled={priceLoading || !query.trim()}
              className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 px-4 py-2 rounded-lg text-sm transition">
              {priceLoading ? "조회중..." : "현재가 조회"}
            </button>
            {currentPrice !== null && (
              <span className="text-white font-bold">
                {isForeign ? `$${currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : `${currentPrice.toLocaleString()}원`}
              </span>
            )}
            {currentPrice !== null && parseInt(quantity) >= 1 && (
              <span className="text-gray-400 text-sm">
                → {parseInt(quantity)}주 = {isForeign ? `$${(currentPrice * parseInt(quantity)).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : `${(currentPrice * parseInt(quantity)).toLocaleString()}원`}
              </span>
            )}
          </div>

          {/* 수량 */}
          <div>
            <label className="text-xs text-gray-400 mb-1 block">수량 (주)</label>
            <input
              type="number" min={1} value={quantity}
              onChange={e => setQuantity(e.target.value)}
              className="bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-sm w-32 outline-none focus:border-blue-500"
            />
            {selectedPos && (
              <span className="text-xs text-gray-500 ml-3">보유 {selectedPos.quantity}주 · 평균단가 {isForeign ? `$${selectedPos.avg_price.toFixed(2)}` : `${Math.round(selectedPos.avg_price).toLocaleString()}원`}</span>
            )}
          </div>

          {/* 매수/매도 버튼 */}
          <div className="flex gap-3">
            <button onClick={handleBuy} disabled={loading || !query.trim()}
              className="flex-1 bg-red-600 hover:bg-red-500 disabled:opacity-50 py-2.5 rounded-xl font-semibold text-sm transition">
              {loading ? "처리중..." : "📈 가상 매수"}
            </button>
            <button onClick={handleSell} disabled={loading || !query.trim() || !selectedPos}
              className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 py-2.5 rounded-xl font-semibold text-sm transition">
              {loading ? "처리중..." : "📉 가상 매도"}
            </button>
          </div>
          <p className="text-xs text-gray-600 text-center">실제 주문이 아닌 가상 체결입니다.</p>
        </div>
      )}

      {/* ── 보유 종목 탭 ── */}
      {subTab === "positions" && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          {positions.length === 0 ? (
            <p className="text-center text-gray-500 py-8">보유 종목이 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-400 border-b border-gray-800">
                    {["종목", "평균단가", "현재가", "수량", "평가손익", "수익률", "비중", ""].map(h => (
                      <th key={h} className="py-2 px-2 text-left font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map(pos => {
                    const fg = pos.market === "US";
                    const plColor = pos.profit_loss >= 0 ? "text-red-400" : "text-blue-400";
                    const isSellOpen = sellTarget?.ticker === pos.ticker && sellTarget?.market === pos.market;
                    return (
                      <React.Fragment key={`${pos.market}:${pos.ticker}`}>
                        <tr className="border-b border-gray-800 hover:bg-gray-800/40">
                          <td className="py-2 px-2">
                            <p className="font-medium text-white">{pos.name}</p>
                            <p className="text-gray-500">{pos.ticker} · {pos.market}</p>
                          </td>
                          <td className="py-2 px-2">{fg ? `$${pos.avg_price.toFixed(2)}` : `${Math.round(pos.avg_price).toLocaleString()}`}</td>
                          <td className="py-2 px-2">
                            <span className={pos.price_stale ? "text-gray-500" : ""}>
                              {fg ? `$${pos.current_price.toFixed(2)}` : `${Math.round(pos.current_price).toLocaleString()}`}
                            </span>
                            {pos.price_stale && <span className="text-gray-600 ml-0.5" title="현재가 조회 실패 — 평균단가로 표시">?</span>}
                          </td>
                          <td className="py-2 px-2">{pos.quantity}</td>
                          <td className={`py-2 px-2 ${plColor}`}>
                            {pos.profit_loss >= 0 ? "+" : ""}{Math.round(pos.profit_loss).toLocaleString()}원
                          </td>
                          <td className={`py-2 px-2 font-semibold ${plColor}`}>{pct(pos.profit_rate)}</td>
                          <td className="py-2 px-2 text-gray-400">{pos.weight.toFixed(1)}%</td>
                          <td className="py-2 px-2">
                            <div className="flex gap-1.5 items-center">
                              {onJudgeHolding && (
                                <button
                                  onClick={() => onJudgeHolding(pos.ticker, pos.name, pos.market, pos.avg_price, pos.quantity)}
                                  className="bg-purple-800 hover:bg-purple-700 text-purple-200 px-2 py-1 rounded text-xs whitespace-nowrap">
                                  보유 판단
                                </button>
                              )}
                              <button
                                onClick={() => {
                                  if (isSellOpen) {
                                    setSellTarget(null);
                                  } else {
                                    setSellTarget({ market: pos.market as "KR" | "US", ticker: pos.ticker, name: pos.name, maxQty: pos.quantity });
                                    setSellQty(String(pos.quantity));
                                  }
                                }}
                                className={`px-2 py-1 rounded text-xs whitespace-nowrap transition ${isSellOpen ? "bg-gray-600 text-gray-300" : "bg-blue-800 hover:bg-blue-700 text-blue-200"}`}>
                                {isSellOpen ? "취소" : "매도"}
                              </button>
                            </div>
                          </td>
                        </tr>
                        {isSellOpen && (
                          <tr className="bg-gray-800/60 border-b border-gray-700">
                            <td colSpan={8} className="px-3 py-3">
                              <div className="flex items-center gap-3 flex-wrap">
                                <span className="text-gray-300 text-xs font-medium">{pos.name} 매도</span>
                                <div className="flex items-center gap-1.5">
                                  <label className="text-gray-400 text-xs">수량</label>
                                  <input
                                    type="number" min={1} max={pos.quantity}
                                    value={sellQty}
                                    onChange={e => setSellQty(e.target.value)}
                                    className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs w-20 outline-none focus:border-blue-500"
                                  />
                                  <span className="text-gray-500 text-xs">/ {pos.quantity}주</span>
                                </div>
                                <button
                                  onClick={() => setSellQty(String(pos.quantity))}
                                  className="text-xs text-gray-400 hover:text-white border border-gray-600 rounded px-2 py-1">
                                  전량
                                </button>
                                <button
                                  onClick={handleQuickSell}
                                  disabled={sellLoading}
                                  className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded text-xs font-semibold transition">
                                  {sellLoading ? "처리중..." : "매도 확인"}
                                </button>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── 거래 내역 탭 ── */}
      {subTab === "history" && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          {trades.length === 0 ? (
            <p className="text-center text-gray-500 py-8">거래 내역이 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-400 border-b border-gray-800">
                    {["시간", "구분", "종목", "체결가", "수량", "거래금액", "실현손익"].map(h => (
                      <th key={h} className="py-2 px-2 text-left font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => {
                    const fg = t.market === "US";
                    const isBuy = t.trade_type === "BUY";
                    const isDeposit = t.trade_type === "DEPOSIT";
                    return (
                      <tr key={i} className="border-b border-gray-800 hover:bg-gray-800/40">
                        <td className="py-2 px-2 text-gray-500">{t.created_at.slice(5)}</td>
                        <td className={`py-2 px-2 font-semibold ${isDeposit ? "text-emerald-400" : isBuy ? "text-red-400" : "text-blue-400"}`}>
                          {isDeposit ? "입금" : isBuy ? "매수" : "매도"}
                        </td>
                        <td className="py-2 px-2">
                          <p className="text-white">{t.name}</p>
                          <p className="text-gray-500">{t.ticker}</p>
                        </td>
                        <td className="py-2 px-2">{fg ? `$${t.price.toFixed(2)}` : `${Math.round(t.price).toLocaleString()}`}</td>
                        <td className="py-2 px-2">{t.quantity}</td>
                        <td className="py-2 px-2">{fg ? `$${t.amount.toFixed(0)}` : `${Math.round(t.amount).toLocaleString()}`}</td>
                        <td className="py-2 px-2">
                          {t.realized_profit != null ? (
                            <span className={t.realized_profit >= 0 ? "text-red-400" : "text-blue-400"}>
                              {t.realized_profit >= 0 ? "+" : ""}{Math.round(t.realized_profit).toLocaleString()}원
                              {t.realized_profit_rate != null && ` (${pct(t.realized_profit_rate)})`}
                            </span>
                          ) : <span className="text-gray-600">—</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
