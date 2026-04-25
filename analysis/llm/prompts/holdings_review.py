"""장중 보유종목 재평가 프롬프트 — LLM Tier1 기반 HOLD/SELL/ADD_BUY + 임계값 동적 조정

30분 간격 장중 사이클에서 보유종목 전체를 LLM에 전달하여
보유 논거 유효성 + 손절/익절 임계값 적정성을 함께 판단한다.
"""

HOLDINGS_REVIEW_SYSTEM = """당신은 한국 주식 장중 보유종목 재평가 전문가입니다.
보유종목 데이터와 시장 상황을 분석하여 종목별로 HOLD/SELL/ADD_BUY를 판단하고,
현재 설정된 손절/익절 임계값이 시장 국면에 적합한지 평가합니다.

## 판단 프레임워크
1. **손익 상태**: 현재 수익률 vs 손절가/목표가 위치
2. **보유일 vs 최대보유일**: 잔여 보유 여유
3. **AI 신뢰도**: 매수 시점의 분석 신뢰도
4. **전략 특성**: 전략별 손절/보유 기간
5. **시장 국면 변화**: 매수 시점 대비 현재 국면이 악화되었는지
6. **임계값 적정성**: 현재 stop_loss/take_profit이 시장 상황에 맞는지

## 임계값 조정 가이드
- 시장 BULL→BEAR 전환: 손절선 타이트하게 (예: -3% → -1.5%)
- 수익 중 + 추세 약화: 익절선 낮춰서 이익 확보 (예: +5% → +3%)
- 강한 상승 추세: 손절선 올려서 이익 보호 (트레일링 효과)
- **trailing_stop_pct 조정**: 차트 변동성/추세 기반
  - 변동성 확대 구간: trailing 넓게 (3~4%) — 노이즈 청산 방지
  - 변동성 수축 + 수익 중: trailing 타이트 (1.5~2%) — 수익 보호
  - 강한 모멘텀: trailing 넓게 유지 — 추세 따라감
- 조정하지 않아도 되면 adjusted 필드를 null로 반환

## 거래 세션 인지 (KRX vs NXT)
시장 컨텍스트에 "거래소: NXT 프리마켓/애프터마켓" 표기가 있으면 다음 제약 고려:
- **KRX_ONLY 종목**(NXT 미지원)은 NXT 세션 중 실제 매도 집행 불가 → SELL 판정해도 다음 KRX 개장(09:00) 후 집행됨. 긴급 SELL 사유면 이유를 명시
- **NXT 유동성 낮음** — 임계값 조정 시 단일가 매매 특성상 급격한 체결 가능, 손절선 여유 확보
- 동일 종목이 KRX/NXT 양쪽 가능하면 세션별 유동성 차이 반영

## 수수료/세금 인지 (필수)
한국 주식 매매는 왕복 비용 약 0.20%(KOSPI 매도세 0.20% + KIS 수수료 0.0028%, KOSDAQ 매도세 0.18%)가 자동 차감됨. 즉 **현재 수익률이 +0.2% 미만이면 매도 시 실질 손실**이 됨. SELL 판단 시:
- 현재 수익률 < +0.4%인데 SELL → 비용 차감 후 본전 또는 손실 → 명확한 추세 악화 근거 필요
- 손절(stop_loss) 발동 SELL은 비용 무시, 위험 제어 우선
- HOLD 권장 시: "본전(+0.21% 이상) 도달 후 매도" 가이드를 reason에 명시 가능

## 금지 사항
- "보수적으로 SELL" 편향 판단 금지 — 데이터 근거로만 판단
- 시장 국면만으로 전량 SELL 판정 금지 — 종목별 개별 판단
- **미세 수익(+0.1~0.2%) 익절 매도 금지** — 비용 차감 후 손실
- 반드시 한국어로 답변"""

HOLDINGS_REVIEW_PROMPT = """## 장중 보유종목 재평가

### 시장 국면
{market_regime}

### 시장 상황
{market_context}

### 잔여 거래 시간
{minutes_left}분

### 보유종목 현황
{holdings_detail}

---

위 데이터를 종합하여 종목별로 판정하세요.
임계값이 현재 시장 국면에 부적합하다면 조정값을 제시하세요.

JSON:
```json
{{"decisions": [
  {{"symbol": "종목코드",
   "action": "HOLD | SELL | ADD_BUY",
   "reason": "판단 근거 1~2문장",
   "confidence": 0.00,
   "adjusted_stop_loss_price": null,
   "adjusted_take_profit_price": null,
   "adjusted_trailing_stop_pct": null
  }}
]}}
```"""


def build_holdings_review_prompt(
    holdings_data: list[dict],
    market_regime: str = "",
    market_context: str = "",
    minutes_left: int = 0,
) -> str:
    """장중 보유종목 재평가용 유저 프롬프트 생성

    Args:
        holdings_data: 종목별 데이터 딕셔너리 리스트
        market_regime: 시장 국면 (BULL/BEAR/SIDEWAYS/THEME)
        market_context: 시장 상황 요약 텍스트
        minutes_left: 강제 청산까지 잔여 분

    Returns:
        포맷된 프롬프트 문자열
    """
    lines = []
    for i, d in enumerate(holdings_data, 1):
        pnl_rate = d.get("pnl_rate", 0.0)
        target_text = f"{d['target_price']:,.0f}원" if d.get("target_price") else "미설정"
        stop_text = f"{d['stop_loss_price']:,.0f}원" if d.get("stop_loss_price") else "미설정"

        # 현재 event_detector에 설정된 실제 임계값 표시
        active_sl = d.get("active_stop_loss")
        active_tp = d.get("active_take_profit")
        active_sl_text = f"{active_sl:,.0f}원" if active_sl and active_sl > 0 else "미설정"
        active_tp_text = f"{active_tp:,.0f}원" if active_tp and active_tp > 0 else "미설정"

        trailing_text = f"{d.get('active_trailing_stop_pct', 0):.1f}%" if d.get("active_trailing_stop_pct") else "미설정"
        chart_text = d.get("chart_analysis", "")

        block = (
            f"#### {i}. {d.get('stock_name', '')} ({d['symbol']})\n"
            f"- 매입가: {d.get('avg_price', 0):,.0f}원 → 현재가: {d.get('current_price', 0):,.0f}원\n"
            f"- 수익률: {pnl_rate:+.2f}%\n"
            f"- 보유수량: {d.get('quantity', 0)}주\n"
            f"- 보유일수: {d.get('hold_days', 0)}일 / 최대 {d.get('max_hold_days', 0)}일\n"
            f"- AI 신뢰도: {d.get('confidence', 0):.2f}\n"
            f"- 목표가: {target_text} | 손절가: {stop_text}\n"
            f"- 현재 활성 익절가: {active_tp_text} | 활성 손절가: {active_sl_text} | 트레일링: {trailing_text}\n"
            f"- 전략: {d.get('strategy_type', 'N/A')}"
        )
        if chart_text:
            block += f"\n- 차트/기술적 분석:\n{chart_text}"

        lines.append(block)

    holdings_detail = "\n\n".join(lines) if lines else "보유종목 없음"
    regime_text = market_regime if market_regime else "정보 없음"
    context_text = market_context if market_context else "정보 없음"

    return HOLDINGS_REVIEW_PROMPT.format(
        market_regime=regime_text,
        market_context=context_text,
        minutes_left=minutes_left,
        holdings_detail=holdings_detail,
    )
