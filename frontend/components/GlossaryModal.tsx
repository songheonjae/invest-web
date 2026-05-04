"use client";

import { useState } from "react";

const TERMS: { term: string; desc: string }[] = [
  { term: "주가", desc: "주식 한 주의 현재 시장 거래 가격. '현재가'라고도 함." },
  { term: "시가총액", desc: "주가 × 총 발행 주식 수. 회사 전체의 시장 가치. 예) 1만원짜리 주식이 1억 주면 시총 1조원." },
  { term: "PER", desc: "주가수익비율(Price Earnings Ratio). 주가 ÷ EPS(주당순이익). 이 회사의 이익 대비 주가가 몇 배인지. 낮을수록 상대적으로 저평가 가능성." },
  { term: "PBR", desc: "주가순자산비율(Price Book-value Ratio). 주가 ÷ BPS(주당순자산). 1배 이하면 장부 가치보다 싸게 거래 중." },
  { term: "EPS", desc: "주당순이익(Earnings Per Share). 당기순이익 ÷ 총 발행 주식 수. 주식 1주가 한 해 동안 얼마를 벌었는지." },
  { term: "BPS", desc: "주당순자산(Book-value Per Share). 순자산 ÷ 총 발행 주식 수. 회사가 지금 청산하면 주주가 1주당 받을 수 있는 금액." },
  { term: "밸류에이션", desc: "기업의 적정 가치를 평가하는 것. 'PER/PBR 기준으로 비싸다/싸다'가 밸류에이션 판단." },
  { term: "매출액", desc: "회사가 영업활동으로 번 총 수입. 비용 차감 전 숫자." },
  { term: "영업이익", desc: "매출액에서 매출원가·판관비 등 영업비용을 뺀 이익. 본업에서 얼마를 버는지." },
  { term: "당기순이익", desc: "모든 비용·세금·이자 등을 다 뺀 최종 이익. '순이익'이라고도 함." },
  { term: "영업이익률", desc: "영업이익 ÷ 매출액 × 100. 매출 100원당 본업에서 얼마나 남기는지. 높을수록 수익성이 좋음." },
  { term: "순이익률", desc: "당기순이익 ÷ 매출액 × 100. 매출에서 최종적으로 얼마나 남는지." },
  { term: "ROE", desc: "자기자본이익률(Return On Equity). 당기순이익 ÷ 자기자본 × 100. 주주 돈으로 얼마나 효율적으로 이익을 냈는지. 15% 이상이면 우수." },
  { term: "흑자", desc: "이익이 플러스(+)인 상태. 돈을 벌고 있다는 뜻." },
  { term: "적자", desc: "이익이 마이너스(-)인 상태. 돈을 잃고 있다는 뜻." },
  { term: "턴어라운드", desc: "적자에서 흑자로 전환되는 것. 실적이 바닥을 찍고 반등하는 구간. 주가가 크게 움직이는 시점." },
  { term: "컨센서스", desc: "여러 증권사 애널리스트들이 예측한 실적 추정치의 평균. '컨센 대비 상회/하회'처럼 씀." },
  { term: "추정치", desc: "아직 발표되지 않은 미래 실적을 예측한 값. (E) 표시가 붙으면 추정치." },
  { term: "가이던스", desc: "회사가 직접 제시하는 향후 실적 전망치. 컨센서스보다 신뢰도 높음." },
  { term: "목표주가", desc: "증권사 애널리스트가 분석 후 제시하는 적정 주가. 현재가보다 높으면 매수 추천 의미." },
  { term: "외국인 비율", desc: "전체 주식 중 외국인이 보유한 비율(%). 외국인 보유율이 높으면 글로벌 관심 종목." },
  { term: "기관", desc: "연기금·펀드·보험사 등 거액을 운용하는 전문 투자자들. 개인보다 정보력이 높다고 여겨짐." },
  { term: "개인", desc: "일반 개인 투자자. '개미'라고도 불림." },
  { term: "수급", desc: "주식을 사려는 힘(수요)과 팔려는 힘(공급)의 균형. 외국인·기관이 사면 수급이 좋다고 표현." },
  { term: "순매수", desc: "매수금액 - 매도금액이 플러스인 상태. 외국인 순매수 = 외국인이 전체적으로 사고 있음." },
  { term: "순매도", desc: "매도금액 - 매수금액이 플러스인 상태. 전체적으로 팔고 있음." },
  { term: "거래량", desc: "특정 기간 동안 거래된 주식 수. 거래량이 급증하면 큰 이슈가 있거나 큰 손이 움직이는 신호." },
  { term: "52주 최고가", desc: "최근 1년(52주) 동안의 최고 주가. 현재가가 이 근처면 강한 저항선이 될 수 있음." },
  { term: "52주 최저가", desc: "최근 1년(52주) 동안의 최저 주가. 현재가가 이 근처면 강한 지지선이 될 수 있음." },
  { term: "이동평균선", desc: "특정 기간 주가의 평균을 선으로 이은 것. MA5(5일), MA20(20일), MA60(60일) 등. 주가가 이동평균 위면 상승 추세." },
  { term: "지지선", desc: "주가가 내려오다가 멈추는 가격대. 바닥 역할을 하는 가격." },
  { term: "저항선", desc: "주가가 올라가다가 막히는 가격대. 천장 역할을 하는 가격." },
  { term: "RSI", desc: "상대강도지수(Relative Strength Index). 0~100 사이 값. 70 이상이면 과매수(조정 가능), 30 이하면 과매도(반등 가능). 14일 기준이 일반적." },
  { term: "MACD", desc: "이동평균 수렴·확산(Moving Average Convergence Divergence). 단기(12일) EMA - 장기(26일) EMA. 플러스면 상승 모멘텀, 마이너스면 하락 모멘텀." },
  { term: "시그널(Signal)", desc: "MACD의 9일 이동평균선. MACD가 시그널을 위로 돌파하면 매수 신호, 아래로 뚫으면 매도 신호." },
  { term: "MACD 히스토그램", desc: "MACD - 시그널의 차이를 막대로 표시. 양수(초록)이면 상승 힘이 강해지는 중, 음수(빨강)이면 하락 힘이 강해지는 중." },
  { term: "볼린저밴드", desc: "20일 이동평균선 ± 표준편차 2배로 만든 상·하단 밴드. 주가가 상단 근처면 단기 과열, 하단 근처면 과매도 가능성. 밴드 폭이 좁아지면 곧 큰 움직임 예고." },
  { term: "골든크로스", desc: "단기 이동평균(MA5)이 장기 이동평균(MA20)을 아래에서 위로 돌파. 상승 전환 신호." },
  { term: "데드크로스", desc: "단기 이동평균(MA5)이 장기 이동평균(MA20)을 위에서 아래로 돌파. 하락 전환 신호." },
  { term: "정배열", desc: "MA5 > MA20 > MA60 순서로 정렬된 상태. 상승 추세가 강하다는 신호." },
  { term: "역배열", desc: "MA5 < MA20 < MA60 순서로 정렬된 상태. 하락 추세가 강하다는 신호." },
  { term: "EMA", desc: "지수이동평균(Exponential Moving Average). 최근 데이터에 더 높은 가중치를 두는 이동평균. MACD 계산에 사용." },
  { term: "과매수", desc: "단기간에 주가가 너무 많이 올라 조정이 필요한 상태. RSI 70 이상이 기준." },
  { term: "과매도", desc: "단기간에 주가가 너무 많이 내려 반등 가능성이 있는 상태. RSI 30 이하가 기준." },
  { term: "모멘텀", desc: "주가가 한 방향으로 계속 움직이려는 힘. '상승 모멘텀이 강하다' = 오르는 힘이 계속되고 있다." },
  { term: "눌림", desc: "상승 추세 중 일시적으로 주가가 소폭 하락하는 것. '눌림목에서 매수'는 잠깐 빠질 때 사는 전략." },
  { term: "추격매수", desc: "이미 많이 오른 주가를 쫓아가며 사는 것. 고점에서 물릴 위험이 높아 일반적으로 주의 필요." },
  { term: "분할매수", desc: "한 번에 다 사지 않고 여러 번에 나눠 사는 것. 평균 매입가를 낮출 수 있어 리스크 관리에 유리." },
  { term: "손절", desc: "손실이 발생한 상태에서 더 큰 손실을 막기 위해 매도하는 것. '손절선'은 미리 정해둔 손절 가격." },
  { term: "리스크", desc: "투자에서 예상치 못한 손실이 발생할 가능성. '리스크가 크다' = 불확실성이 높다." },
  { term: "상방 여력", desc: "현재 주가에서 앞으로 오를 수 있는 여지. '상방 여력이 크다' = 더 오를 가능성이 높다." },
  { term: "하방 위험", desc: "현재 주가에서 떨어질 수 있는 위험. '하방 위험이 크다' = 떨어질 가능성이 높다." },
  { term: "선반영", desc: "아직 일어나지 않은 기대(호재/악재)가 이미 주가에 반영된 것. '이미 선반영됐다' = 기대가 이미 주가에 다 녹아있다." },
  { term: "실적 서프라이즈", desc: "시장 예상치(컨센서스)를 크게 웃도는 실적 발표. 주가 급등의 촉매가 됨. '어닝 서프라이즈'라고도 함." },
  { term: "어닝 쇼크", desc: "시장 예상치를 크게 밑도는 실적 발표. 주가 급락의 원인이 됨." },
];

export default function GlossaryModal() {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const filtered = TERMS.filter(t =>
    t.term.includes(search) || t.desc.includes(search)
  );

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="text-xs text-gray-400 hover:text-purple-400 border border-gray-700 hover:border-purple-600 px-3 py-1.5 rounded-lg transition"
      >
        📖 용어해설집
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setOpen(false)}>
          <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
            {/* 헤더 */}
            <div className="flex items-center justify-between p-5 border-b border-gray-800">
              <h2 className="font-bold text-lg text-white">📖 주식 용어해설집</h2>
              <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-white text-xl">✕</button>
            </div>

            {/* 검색 */}
            <div className="px-5 pt-4">
              <input
                type="text"
                placeholder="용어 검색..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
              />
            </div>

            {/* 목록 */}
            <div className="overflow-y-auto flex-1 p-4 space-y-1">
              {filtered.map(({ term, desc }) => (
                <div key={term}>
                  <button
                    onClick={() => setSelected(selected === term ? null : term)}
                    className={`w-full text-left px-4 py-2.5 rounded-lg text-sm transition flex items-center justify-between ${
                      selected === term
                        ? "bg-purple-900/50 text-purple-200 border border-purple-700"
                        : "text-gray-300 hover:bg-gray-800"
                    }`}
                  >
                    <span className="font-medium">{term}</span>
                    <span className="text-gray-500 text-xs">{selected === term ? "▲" : "▼"}</span>
                  </button>
                  {selected === term && (
                    <div className="mx-2 mb-1 px-4 py-3 bg-gray-800 rounded-b-lg text-xs text-gray-300 leading-relaxed">
                      {desc}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
