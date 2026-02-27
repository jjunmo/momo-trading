"""뉴스 감성 분석 (Phase 7에서 확장)"""
from loguru import logger

from analysis.llm.llm_factory import llm_factory
from trading.enums import LLMTier


class NewsAnalyzer:
    """AI 기반 뉴스 감성 분석"""

    async def analyze_sentiment(self, stock_name: str, news_items: list[str]) -> dict:
        """뉴스 헤드라인 감성 분석"""
        if not news_items:
            return {"sentiment": "NEUTRAL", "score": 0.5, "summary": "뉴스 데이터 없음"}

        prompt = f"""## 뉴스 감성 분석: {stock_name}

### 최근 뉴스 헤드라인
{chr(10).join(f"- {n}" for n in news_items[:10])}

위 뉴스를 분석하여 해당 종목에 대한 감성을 평가해주세요.

JSON 형식으로 답변:
```json
{{
  "sentiment": "POSITIVE",
  "score": 0.7,
  "summary": "긍정적 뉴스 우세. 실적 호조 기대감."
}}
```

sentiment: POSITIVE | NEUTRAL | NEGATIVE
score: 0.0 (매우 부정) ~ 1.0 (매우 긍정)"""

        try:
            result, provider = await llm_factory.generate_tier1(prompt)
            import json
            # JSON 파싱 시도
            start = result.find("{")
            end = result.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(result[start:end])
        except Exception as e:
            logger.error("뉴스 감성 분석 오류: {}", str(e))

        return {"sentiment": "NEUTRAL", "score": 0.5, "summary": "분석 실패"}


news_analyzer = NewsAnalyzer()
