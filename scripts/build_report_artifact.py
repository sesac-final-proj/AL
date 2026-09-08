from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ANALYSIS_DIR / "output"
DATA_PATH = OUTPUT_DIR / "market_products_analysis_dataset.csv"
SUMMARY_PATH = OUTPUT_DIR / "quality_summary.json"
ARTIFACT_PATH = ANALYSIS_DIR / "artifact.json"
DATABASE_PATH = OUTPUT_DIR / "market_analysis.sqlite"


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records", force_ascii=False))


def main() -> None:
    data = pd.read_csv(DATA_PATH, encoding="utf-8-sig")
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    quality = pd.DataFrame(summary["quality"])
    integrated_quality = quality.loc[quality["dataset"].eq("integrated_market_products")].iloc[0]
    joong_quality = quality.loc[quality["dataset"].eq("joonggonara")].iloc[0]

    quality_table = quality[[
        "dataset", "input_rows", "output_rows", "exact_duplicates_removed",
        "reference_match_rate_pct", "district_unknown_rate_pct", "price_missing",
        "component_rows", "approximate_date_rows",
    ]].rename(columns={
        "dataset": "데이터셋",
        "input_rows": "입력행",
        "output_rows": "출력행",
        "exact_duplicates_removed": "제거중복",
        "reference_match_rate_pct": "기준매칭률",
        "district_unknown_rate_pct": "구미상률",
        "price_missing": "가격결측",
        "component_rows": "부품행",
        "approximate_date_rows": "대략날짜행",
    })

    platform_sql = """
WITH valid AS (
  SELECT * FROM listings
  WHERE 가격원 IS NOT NULL AND 가격신뢰 = '신뢰' AND 완제품여부 = '완제품'
), ranked AS (
  SELECT 플랫폼, 가격원,
         ROW_NUMBER() OVER (PARTITION BY 플랫폼 ORDER BY 가격원) AS rn,
         COUNT(*) OVER (PARTITION BY 플랫폼) AS n
  FROM valid
), price AS (
  SELECT 플랫폼, COUNT(*) AS 유효표본수,
         AVG(가격원) AS 가격평균,
         AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN 가격원 END) AS 가격중앙값
  FROM ranked GROUP BY 플랫폼
), volume AS (
  SELECT 플랫폼, COUNT(*) AS 게시물수,
         SUM(CASE WHEN 구 = '미상' THEN 1 ELSE 0 END) AS 구미상건수
  FROM listings GROUP BY 플랫폼
)
SELECT volume.플랫폼, 게시물수, 구미상건수, 유효표본수,
       ROUND(가격중앙값) AS 가격중앙값, ROUND(가격평균) AS 가격평균
FROM volume LEFT JOIN price USING (플랫폼)
ORDER BY 게시물수 DESC
""".strip()
    category_sql = """
WITH valid AS (
  SELECT * FROM listings
  WHERE 가격원 IS NOT NULL AND 가격신뢰 = '신뢰' AND 완제품여부 = '완제품'
), ranked AS (
  SELECT 카테고리, 가격원,
         ROW_NUMBER() OVER (PARTITION BY 카테고리 ORDER BY 가격원) AS rn,
         COUNT(*) OVER (PARTITION BY 카테고리) AS n
  FROM valid
)
SELECT 카테고리, COUNT(*) AS 유효표본수,
       ROUND(AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN 가격원 END)) AS 가격중앙값,
       ROUND(AVG(가격원)) AS 가격평균
FROM ranked GROUP BY 카테고리 ORDER BY 유효표본수 DESC
""".strip()
    headline_sql = "SELECT COUNT(*) AS 분석행 FROM listings"
    quality_sql = "SELECT * FROM quality_summary"

    with sqlite3.connect(DATABASE_PATH) as connection:
        data.to_sql("listings", connection, if_exists="replace", index=False)
        quality_table.to_sql("quality_summary", connection, if_exists="replace", index=False)
        platform_price = pd.read_sql_query(platform_sql, connection)
        category = pd.read_sql_query(category_sql, connection)
        headline = pd.read_sql_query(headline_sql, connection)
        quality_table = pd.read_sql_query(quality_sql, connection)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    def sql_source(source_id: str, label: str, sql: str, description: str) -> dict[str, object]:
        return {
            "id": source_id,
            "label": label,
            "path": "market_analysis/output/market_analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": sql,
                "description": description,
                "tables_used": ["listings" if source_id != "quality_sql" else "quality_summary"],
            },
        }

    headline_source = sql_source("headline_sql", "전처리 게시물 집계", headline_sql, "중복 제거 후 분석 모집단의 행 수를 집계합니다.")
    platform_source = sql_source("platform_sql", "플랫폼별 규모와 가격", platform_sql, "유효 완제품을 기준으로 플랫폼별 게시물 수와 가격 중앙값을 계산합니다.")
    category_source = sql_source("category_sql", "카테고리별 가격", category_sql, "유효 완제품의 카테고리별 표본 수와 가격 중앙값을 계산합니다.")
    quality_source = sql_source("quality_sql", "전처리 품질 요약", quality_sql, "입력·출력 행, 중복, 기준 매칭 및 지역 결측을 비교합니다.")
    sources = [headline_source, platform_source, category_source, quality_source]
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "중고거래 상품 데이터 전처리 및 분석 보고서",
            "generatedAt": now,
            "cards": [
                {
                    "id": "rows_card", "dataset": "headline_metrics", "sourceId": "headline_sql",
                    "description": "원천 15개 컬럼 기준 정확 중복 제거 후 통합 게시물 수",
                    "metrics": [{"label": "분석 가능 게시물", "field": "분석행", "format": "number"}],
                },
                {
                    "id": "duplicate_card", "dataset": "headline_metrics", "sourceId": "quality_sql",
                    "description": "통합 파일에서 제거한 완전 동일 행",
                    "metrics": [{"label": "제거된 중복", "field": "제거중복", "format": "number"}],
                },
                {
                    "id": "overlap_card", "dataset": "headline_metrics", "sourceId": "quality_sql",
                    "description": "중고나라 고유 행 중 통합 파일에도 존재하는 비율",
                    "metrics": [{"label": "중고나라 포함률", "field": "중고나라포함률", "format": "percent"}],
                },
            ],
            "charts": [
                {
                    "id": "platform_count_chart",
                    "title": "플랫폼별 게시물 수",
                    "type": "bar",
                    "dataset": "platform_price",
                    "sourceId": "platform_sql",
                    "encodings": {
                        "x": {"field": "플랫폼", "type": "nominal"},
                        "y": {"field": "게시물수", "type": "quantitative"},
                    },
                },
                {
                    "id": "platform_price_chart",
                    "title": "플랫폼별 유효 완제품 가격 중앙값",
                    "type": "bar",
                    "dataset": "platform_price",
                    "sourceId": "platform_sql",
                    "encodings": {
                        "x": {"field": "플랫폼", "type": "nominal"},
                        "y": {"field": "가격중앙값", "type": "quantitative", "unit": "원"},
                    },
                },
                {
                    "id": "category_price_chart",
                    "title": "카테고리별 유효 완제품 가격 중앙값",
                    "type": "bar",
                    "dataset": "category_price",
                    "sourceId": "category_sql",
                    "encodings": {
                        "x": {"field": "카테고리", "type": "nominal"},
                        "y": {"field": "가격중앙값", "type": "quantitative", "unit": "원"},
                    },
                },
            ],
            "tables": [
                {
                    "id": "quality_table",
                    "title": "데이터셋별 품질 점검",
                    "dataset": "quality",
                    "sourceId": "quality_sql",
                    "columns": [
                        {"field": "데이터셋", "label": "데이터셋"},
                        {"field": "입력행", "label": "입력 행", "format": "number"},
                        {"field": "출력행", "label": "출력 행", "format": "number"},
                        {"field": "제거중복", "label": "제거 중복", "format": "number"},
                        {"field": "구미상률", "label": "구 미상률 (%)", "format": "number"},
                    ],
                },
            ],
            "sources": sources,
            "blocks": [
                {"id": "title", "type": "markdown", "body": "# 중고거래 상품 데이터 전처리 및 분석 보고서"},
                {
                    "id": "technical_summary", "type": "markdown", "sourceId": "quality_sql",
                    "body": (
                        "## 핵심 결과\n\n"
                        f"- 통합 파일 {int(integrated_quality['input_rows']):,}건에서 정확 중복 "
                        f"{int(integrated_quality['exact_duplicates_removed']):,}건을 제거해 **{int(integrated_quality['output_rows']):,}건**을 분석 모집단으로 확정했습니다.\n"
                        f"- 중고나라 고유 행 {summary['overlap']['joonggonara_unique_rows']:,}건은 통합 파일에 **100% 포함**되어 두 파일을 단순 결합하지 않았습니다.\n"
                        f"- 통합 가격 중앙값은 {int(integrated_quality['price_median_won']):,}원, 중고나라는 {int(joong_quality['price_median_won']):,}원입니다.\n"
                        f"- `구=미상` 비율은 통합 {integrated_quality['district_unknown_rate_pct']:.2f}%, 중고나라 {joong_quality['district_unknown_rate_pct']:.2f}%로 지역 분석의 핵심 제약입니다."
                    ),
                },
                {"id": "headline_metrics", "type": "metric-strip", "cardIds": ["rows_card", "duplicate_card", "overlap_card"]},
                {
                    "id": "platform_finding", "type": "markdown", "sourceId": "platform_sql",
                    "body": "## 통합 파일이 전체 분석 기준입니다\n\n중고나라 파일은 독립 비교용으로 유지하되, 전체 시장 규모·구성 분석에는 통합 파일만 사용해야 이중 집계를 피할 수 있습니다. 아래 막대는 중복 제거 후 통합 모집단의 플랫폼 구성을 보여줍니다.",
                },
                {"id": "platform_count", "type": "chart", "chartId": "platform_count_chart"},
                {
                    "id": "price_finding", "type": "markdown", "sourceId": "platform_sql",
                    "body": "## 가격은 플랫폼과 카테고리를 함께 봐야 합니다\n\n가격 비교는 `가격신뢰=신뢰`이면서 `완제품여부=완제품`인 행만 사용했습니다. 중앙값은 고가 이상치의 영향을 줄이며, 플랫폼별 상품 구성 차이가 남아 있으므로 같은 카테고리·세부유형 안에서 재비교하는 것이 안전합니다.",
                },
                {"id": "platform_price", "type": "chart", "chartId": "platform_price_chart"},
                {
                    "id": "category_finding", "type": "markdown", "sourceId": "category_sql",
                    "body": "## 카테고리별 가격 기준선이 크게 다릅니다\n\n카테고리 중앙값은 상품군별 기준선을 제공합니다. 플랫폼 효과를 해석할 때 이 구성 차이를 통제하지 않으면 가격 차이를 플랫폼 차이로 잘못 볼 수 있습니다.",
                },
                {"id": "category_price", "type": "chart", "chartId": "category_price_chart"},
                {
                    "id": "scope", "type": "markdown",
                    "body": "## 범위와 지표 정의\n\n분석 단위는 판매 게시물 1건입니다. 원천 15개 컬럼을 기준으로 정확 중복을 제거했고, 기준 파일과 동일한 `구`, `가격원`, `완제품여부`, `세부유형`, `가격신뢰`, `날짜정밀도`를 생성했습니다. 분석용 통합 파일에는 출처 URL을 바탕으로 `플랫폼`을 추가했습니다.",
                },
                {"id": "quality_heading", "type": "markdown", "body": "## 품질 점검 결과\n\n기준 파일과 정확히 일치하는 행은 기존 파생값을 재사용했고, 나머지는 규칙 기반으로 보완했습니다. 표의 구 미상률과 기준 매칭률은 후속 분석에서 반드시 필터 또는 민감도 조건으로 사용해야 합니다."},
                {"id": "quality", "type": "table", "tableId": "quality_table"},
                {
                    "id": "methodology", "type": "markdown",
                    "body": "## 전처리 방법\n\n1. UTF-8 BOM으로 읽고 Unicode NFKC 및 공백을 정규화했습니다.\n2. 원천 15개 컬럼 전체가 같은 행을 중복으로 판정했습니다.\n3. 기준 당근 파일과 일치하는 행은 여섯 파생 컬럼을 그대로 재사용했습니다.\n4. 미매칭 행은 가격·지역·제품 상태·모델 키워드 규칙으로 파생값을 만들었습니다.\n5. 가격·채팅·관심·조회는 수치형, 매너온도는 실수형으로 변환했습니다.",
                },
                {
                    "id": "limitations", "type": "markdown",
                    "body": "## 한계와 견고성 점검\n\n- 중고나라의 구 미상률이 높아 구 단위 결과는 해당 플랫폼을 별도 표기해야 합니다.\n- `완제품여부`와 `세부유형`의 미매칭 행은 키워드 규칙이므로 표본 검수가 필요합니다.\n- 플랫폼마다 조회·관심·채팅 수집 정의가 다를 수 있어 절대값의 플랫폼 간 비교는 제한적입니다.\n- 동일 상품의 재등록·유사 제목은 정확 중복 제거만으로 합쳐지지 않습니다.",
                },
                {
                    "id": "next_steps", "type": "markdown",
                    "body": "## 권장 다음 단계\n\n1. `구=미상`을 제외한 결과와 포함한 결과를 함께 산출합니다.\n2. 같은 카테고리·세부유형 안에서 플랫폼 가격 중앙값과 사분위 범위를 비교합니다.\n3. 규칙 기반 `부품` 판정과 `세부유형`을 카테고리별 50건씩 표본 검수합니다.\n4. 판매상태별 가격 차이와 조회→관심→채팅 비율을 플랫폼 내부에서 분석합니다.",
                },
                {
                    "id": "questions", "type": "markdown",
                    "body": "## 후속 질문\n\n- 같은 모델에서 플랫폼별 가격 차이는 얼마나 나는가?\n- 거래완료 게시물은 거래중 게시물보다 가격이 낮은가?\n- 조회 대비 관심·채팅 전환이 높은 카테고리는 무엇인가?\n- 구 정보 보강 후 지역별 가격 차이가 유지되는가?",
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": now,
            "status": "ready",
            "datasets": {
                "headline_metrics": [{
                    "분석행": int(headline.iloc[0]["분석행"]),
                    "제거중복": int(integrated_quality["exact_duplicates_removed"]),
                    "중고나라포함률": summary["overlap"]["coverage_pct"] / 100,
                }],
                "platform_price": records(platform_price),
                "category_price": records(category),
                "quality": records(quality_table),
            },
        },
        "sources": sources,
    }
    ARTIFACT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(ARTIFACT_PATH)


if __name__ == "__main__":
    main()
