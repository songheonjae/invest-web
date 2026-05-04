import axios from "axios";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
});

export interface StockData {
  ticker: string;
  name: string;
  price: string | number;
  change: string | number;
  change_rate: string | number;
  volume: string | number;
  high: string | number;
  low: string | number;
  open: string | number;
  market_cap?: number;
  per?: number;
}

export async function fetchDomesticStock(ticker: string): Promise<StockData> {
  const res = await api.get(`/stock/domestic/${ticker}`);
  return res.data;
}

export async function fetchForeignStock(ticker: string): Promise<StockData> {
  const res = await api.get(`/stock/foreign/${ticker}`);
  return res.data;
}

export interface SearchResult {
  name: string;
  ticker: string;
}

export async function searchStocks(query: string, market: "domestic" | "foreign"): Promise<SearchResult[]> {
  const res = await api.get(`/stock/search/${market}`, { params: { q: query } });
  return res.data;
}

export interface CandleData {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export async function fetchDomesticChart(ticker: string, period = "D"): Promise<CandleData[]> {
  const res = await api.get(`/stock/chart/domestic/${ticker}`, { params: { period } });
  return res.data;
}

export interface FinancialRecord {
  period: string;
  매출액?: string;
  영업이익?: string;
  당기순이익?: string;
  영업이익률?: string;
  ROE?: string;
  [key: string]: string | undefined;
}

export interface ConfidenceResult {
  total: number;
  reliability: { score: number; max: number; label?: string };
  attractiveness: { score: number; max: number; label?: string };
  is_turnaround?: boolean;
}

export interface TechnicalIndicators {
  rsi?: number;
  rsi_comment?: string;
  macd?: number;
  macd_signal?: number;
  macd_hist?: number;
  macd_comment?: string;
  bb_upper?: number;
  bb_mid?: number;
  bb_lower?: number;
  bb_position?: string;
  ma5?: number;
  ma20?: number;
  ma60?: number;
  ma120?: number;
  trend?: string;
  cross?: string;
  vol_ratio?: number;
  gap_from_high?: number;
  rebound_from_low?: number;
}

export interface AnalysisResult {
  report: string;
  fundamental: Record<string, string>;
  news: { title: string; url: string; date: string; source: string }[];
  financials?: { annual: FinancialRecord[]; quarterly: FinancialRecord[]; unit?: string };
  confidence?: ConfidenceResult;
  indicators?: TechnicalIndicators;
}

export async function analyzeStock(ticker: string, market: "domestic" | "foreign" = "domestic"): Promise<AnalysisResult> {
  const res = await api.get(`/stock/analyze/${market}/${ticker}`);
  return res.data;
}

export async function fetchForeignChart(ticker: string): Promise<CandleData[]> {
  const res = await api.get(`/stock/chart/foreign/${ticker}`);
  return res.data;
}

// ── 추천 ──────────────────────────────────────────────────────

export interface SplitEntry {
  차수: string;
  금액: number;
  조건: string;
}

export interface RecommendationItem {
  ticker: string;
  name: string;
  sector: string;
  tier: number;
  score: number;
  reasons: string[];
  risks: string[];
  buy_type: "즉시 검토형" | "확인 후 매수형" | "관찰 우선형";
  weight_pct: number | null;
  amount_krw: number | null;
  split: SplitEntry[];
}

export interface ExcludedItem {
  ticker: string;
  name: string;
  sector: string;
  score: number;
  exclusion_reason: string;
}

export interface RecommendRequest {
  amount: number;
  risk: "conservative" | "neutral" | "aggressive";
  period: "short" | "medium" | "long";
  market: "KR" | "US" | "ALL";
  recommend_count: number | null;
  prefer_sectors: string[];
  prefer_styles: string[];
  exclude_sectors: string[];
  exclude_tags: string[];
  holdings: string[];
}

export interface RecommendResponse {
  summary: string;
  recommendations: RecommendationItem[];
  excluded: ExcludedItem[];
  notes: string;
  report: string;
}

export async function fetchRecommendations(req: RecommendRequest): Promise<RecommendResponse> {
  const res = await api.post("/recommend", req);
  return res.data;
}

// ── 보유 판단 ─────────────────────────────────────────────────

export type HoldingHorizon    = "short" | "medium" | "long";
export type HoldingBuyReason  = "rebound" | "turnaround" | "long" | "value" | "growth" | "other";

export interface HoldingJudgeRequest {
  ticker:        string;
  avg_buy_price: number;
  quantity:      number;
  horizon:       HoldingHorizon;
  buy_reason:    HoldingBuyReason;
  market:        "domestic" | "foreign";
}

export interface HoldingJudgeResult {
  ticker:            string;
  name:              string;
  current_price:     number;
  avg_buy_price:     number;
  quantity:          number;
  invested_amount:   number;
  evaluation_amount: number;
  profit_loss:       number;
  profit_rate:       number;
  decision:          "계속 보유 가능" | "조건부 보유" | "일부 비중 축소 고려" | "정리 고려";
  position_summary:  string;
  reasons:           string[];
  hold_conditions:   string[];
  reduce_conditions: string[];
  exit_conditions:   string[];
  check_first:       string[];
  warnings:          string[];
  timing_score:      number;
  timing_summary:    string;
  report:            string;
  validation_severity?: string | null;
  confidence_score?:   number | null;
}

export async function judgeHolding(req: HoldingJudgeRequest): Promise<HoldingJudgeResult> {
  const res = await api.post("/holdings/judge", req);
  return res.data;
}

// ── 모의투자 ──────────────────────────────────────────────────

export interface PaperAccount {
  initial_cash: number;
  cash: number;
  deposited: number;
  stock_value: number;
  total_asset: number;
  total_profit_loss: number;
  total_profit_rate: number;
}

export interface PaperPosition {
  market: string;
  ticker: string;
  name: string;
  avg_price: number;
  current_price: number;
  quantity: number;
  cost_amount: number;
  evaluation_amount: number;
  profit_loss: number;
  profit_rate: number;
  weight: number;
}

export interface PaperTrade {
  created_at: string;
  trade_type: "BUY" | "SELL" | "DEPOSIT";
  market: string;
  ticker: string;
  name: string;
  price: number;
  quantity: number;
  amount: number;
  realized_profit: number | null;
  realized_profit_rate: number | null;
}

export async function paperGetAccount(): Promise<PaperAccount> {
  const res = await api.get("/paper/account");
  return res.data;
}

export async function paperResetAccount(initial_cash: number): Promise<{ message: string }> {
  const res = await api.post("/paper/reset", { initial_cash });
  return res.data;
}

export async function paperAddFunds(amount: number): Promise<{ message: string; cash: number; initial_cash: number }> {
  const res = await api.post("/paper/add-funds", { amount });
  return res.data;
}

export async function paperBuy(market: string, ticker: string, name: string, quantity: number): Promise<{ success: boolean; message: string; cash: number }> {
  const res = await api.post("/paper/buy", { market, ticker, name, quantity });
  return res.data;
}

export async function paperSell(market: string, ticker: string, quantity: number): Promise<{ success: boolean; message: string; cash: number; realized_profit: number; realized_profit_rate: number }> {
  const res = await api.post("/paper/sell", { market, ticker, quantity });
  return res.data;
}

export async function paperGetPositions(): Promise<PaperPosition[]> {
  const res = await api.get("/paper/positions");
  return res.data;
}

export async function paperGetTrades(): Promise<PaperTrade[]> {
  const res = await api.get("/paper/trades");
  return res.data;
}
