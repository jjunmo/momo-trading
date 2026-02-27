"""Tier 2: 리스크 평가 프롬프트"""

RISK_ASSESSMENT_SYSTEM = """당신은 주식 리스크 관리 전문가입니다.
매매 결정 전 최종 리스크 평가를 수행합니다.
반드시 한국어로 답변하세요."""

RISK_ASSESSMENT_PROMPT = """## 리스크 평가 요청

### 매매 계획
- 종목: {stock_name} ({symbol})
- 방향: {action}
- 수량: {quantity}주
- 예상 가격: {price:,.0f}원
- 총 금액: {total_amount:,.0f}원

### 현재 포트폴리오 상태
- 총 자산: {total_asset:,.0f}원
- 현금: {cash:,.0f}원
- 보유 종목 수: {holding_count}개
- 이 매매 후 현금 비중: {cash_after_pct:.1f}%
- 이 매매 후 종목 비중: {position_after_pct:.1f}%

### 오늘 매매 현황
- 오늘 매매 횟수: {today_trades}회
- 일일 한도: {max_daily_trades}회

---

## 리스크 체크리스트
1. 단일 종목 비중이 전략 한도를 초과하는가?
2. 현금 비중이 너무 낮아지는가? (최소 30% 유지 권장)
3. 일일 매매 횟수 한도에 근접했는가?
4. 손절 시 예상 손실이 감당 가능한 수준인가?
5. 전체 포트폴리오 리스크가 증가하는가?

JSON 형식으로 답변:
```json
{{
  "risk_level": "LOW",
  "approved": true,
  "warnings": [],
  "adjusted_quantity": 10,
  "reason": "리스크 적정 수준. 진행 가능."
}}
```

risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
- CRITICAL: 매매 불가
- HIGH: 수량 조정 필요
- MEDIUM: 경고와 함께 진행 가능
- LOW: 이상 없음"""
