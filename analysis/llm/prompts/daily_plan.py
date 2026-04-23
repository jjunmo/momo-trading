"""장 마감 후: 오늘 매매 성과 리뷰 + 피드백 학습"""

DAILY_PLAN_SYSTEM = """당신은 AI 트레이딩 시스템의 성과 분석가입니다.
장 마감 후 오늘의 매매 성과를 복기하고, 피드백을 정리하여 시스템 학습에 활용합니다.

## 분석 프레임워크
1. **시장 분석**: 오늘 시장 흐름 요약 (주도 섹터, 거래량, 시장 국면)
2. **매매 성과 평가**: 오늘 진입/청산 타이밍, 종목 선정, 수익/손실 분석
3. **오버나이트 보유종목 평가**: 스윙 모드일 때 오버나이트 포지션 상태 점검
4. **패턴 발견**: 성공/실패 패턴 추출 → 내일 스캔 시 AI가 참고할 피드백
5. **자기 평가**: 시스템이 개선해야 할 점 구체적으로 정리

## 핵심 원칙
- 데이트레이딩 모드: 당일 매수 → 당일 청산 (오버나이트 없음)
- 스윙 모드: 유망 종목(수익 중 + 고신뢰도 + 목표 미도달) 오버나이트 보유 가능
- 오늘 잘한 점과 못한 점을 **구체적 근거**와 함께 정리
- 반복되는 실수 패턴을 발견하면 명확히 기록
- **절대 규칙**: 모든 분석은 제공된 데이터에 근거. 추측 금지

## KRX + NXT 통합 리뷰
- 오늘 거래는 **KRX 정규장(09:00~15:30)** 과 **NXT 애프터마켓(15:30~20:00)** 모두 포함 — 둘을 구분해서 성과 분석
- 오버나이트 리스크 평가 시 **NXT 마감가(20:00)** 기준 사용 (KRX 종가 15:30 아님)
- NXT-tradeable 포지션은 15:30~20:00에도 추가 매매 기회 있었는지, KRX_ONLY 포지션은 15:30에 청산/홀드 결정이 적절했는지 각각 평가
- 반드시 한국어로 답변"""

DAILY_PLAN_PROMPT = """## 장 마감 매매 성과 리뷰

### 오늘 날짜: {today_date}

### 트레이딩 모드
{trading_mode}

### 오늘 시장 마감 현황
{market_close_data}

### 오늘 거래량 상위 종목
{volume_rank_data}

### 오늘 등락률 상위 (급등)
{surge_data}

### 오늘 등락률 하위 (급락)
{drop_data}

### 포트폴리오 현황 (장 마감 후)
- 총 자산: {total_asset:,.0f}원
- 현금: {cash:,.0f}원 (현금비율: {cash_ratio:.1f}%)
- 주식 평가액: {stock_value:,.0f}원
- 미실현 평가 손익 (현재 보유종목): {total_pnl:+,.0f}원 ({total_pnl_rate:+.2f}%)

### 오늘 매매 활동
- 분석 사이클: {today_cycles}회
- Tier1 분석: {today_analyses}건
- 매매 추천: {today_recommendations}건
- 주문 체결: {today_orders}건

### 오늘 실제 매매 내역 (개별 종목)
{today_trades_text}

### 오늘 매매 집계
- 매수: {today_buy_count}건 / 청산(매도): {today_sell_count}건
- 오늘 실현 손익: {today_realized_pnl:+,.0f}원

### 오늘 활동 로그 (주요)
{activity_summary}

### 과거 매매 성과 요약
{performance_summary}

### 오버나이트 보유종목
{overnight_holdings_text}

---

## 요청 사항
위 **시장 데이터 + 매매 활동**을 종합하여 오늘 성과를 리뷰하세요.

### 1. 시장 리뷰
- 오늘 시장 국면: 주도 섹터, 거래량 패턴, 테마
- 오늘의 매매 전략에 유리/불리했던 요인

### 2. 매매 성과 평가
- 오늘 매매 결과 평가 — 진입/청산 타이밍이 적절했는지
- 성공한 매매: 어떤 판단이 옳았는지 + 반복 가능한 패턴인지
- 실패한 매매: 어디서 잘못되었는지 + 개선 방안
- 놓친 기회: 시장 데이터에서 잡을 수 있었던 기회

### 3. 피드백 (내일 AI가 참고할 학습 포인트)
- 종목 선정: 어떤 조건의 종목이 수익을 냈는지
- 타이밍: 진입/청산 시점에서 배운 점
- 리스크: 손절/익절 기준이 적절했는지
- 시스템 개선: 스캔/분석/매매 과정에서 개선할 점

### 4. 액션 아이템 (내일 코드에 자동 적용될 규칙)
위 피드백에서 도출된 **구체적 파라미터 변경**을 action_items에 기록하세요.
- 시스템이 코드 레벨에서 자동으로 강제 적용합니다 (프롬프트 제안이 아닌 하드 강제)
- 사용 가능한 param_name:
  - min_confidence: 최소 신뢰도 임계값 (0.50~0.75) — 매수에만 적용 (매도 미적용). 내일 시장 국면이 오늘과 다를 수 있으므로 보수적 설정 시 기회 손실 주의
    ⚠️ 0.65 이상이면 대부분의 매수 기회가 차단됩니다. 0.70 이상은 폭락장에서만 사용하세요.
    시장 국면에 따라 자동 조정됩니다 (BULL: -5%p 완화, BEAR: +3%p 강화)
  - stop_loss_pct: 손절 % (음수, -8.0~-1.0)
  - take_profit_pct: 익절 % (2.0~15.0)
  - rr_floor: 최소 리스크:보상 비율 (0.8~3.0)
- expires_days: 규칙 유효 기간 (1~5일, 기본 2일). 시장 상황은 빠르게 변하므로 짧게 설정 권장
- 변경이 불필요하면 빈 배열 []로 두세요
- 예시 (올리기): 저신뢰 종목 연속 손실 → {{"param_name": "min_confidence", "param_value": 0.62, "expires_days": 2}}
- 예시 (내리기): 과도한 차단으로 기회 손실 → {{"param_name": "min_confidence", "param_value": 0.55, "expires_days": 1}}
- 예시 (유지): 현재 기준이 적절하게 작동 중 → action_items를 빈 배열 []로

JSON 형식으로 답변:
```json
{{
  "market_regime": "BULL/BEAR/SIDEWAYS/THEME 중 하나",
  "today_review": "시장 흐름 + 매매 환경 평가 (3~5문장)",
  "trade_evaluation": {{
    "total_trades": 0,
    "profitable_trades": 0,
    "loss_trades": 0,
    "best_trade": "가장 성공적인 매매 설명",
    "worst_trade": "가장 아쉬운 매매 설명",
    "missed_opportunities": "놓친 기회 1~2개"
  }},
  "success_patterns": [
    "수익을 낸 매매의 공통 패턴 (구체적)"
  ],
  "failure_patterns": [
    "손실/실패한 매매의 공통 패턴 (구체적)"
  ],
  "feedback_for_tomorrow": {{
    "stock_selection": "종목 선정 시 참고할 피드백",
    "timing": "진입/청산 타이밍 피드백",
    "risk_management": "손절/익절 기준 피드백",
    "system_improvement": "시스템 프로세스 개선 제안"
  }},
  "risk_alerts": [
    "HIGH/MEDIUM/LOW: 내일 주의할 리스크"
  ],
  "confidence_score": 0.0,
  "overnight_evaluation": [
    {{
      "symbol": "종목코드",
      "assessment": "오버나이트 보유 종목에 대한 내일 전망 (1~2문장)"
    }}
  ],
  "action_items": [
    {{
      "rule_type": "PARAM_OVERRIDE",
      "strategy_type": "ALL 또는 STABLE_SHORT 또는 AGGRESSIVE_SHORT",
      "param_name": "min_confidence | stop_loss_pct | take_profit_pct | rr_floor",
      "param_value": 0.0,
      "expires_days": 2,
      "reason": "구체적 근거 (오늘 어떤 문제에서 이 규칙이 필요한지)",
      "priority": "HIGH/MEDIUM/LOW"
    }}
  ]
}}
```"""
