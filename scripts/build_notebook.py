from pathlib import Path

import nbformat as nbf


analysis_dir = Path(__file__).resolve().parent
notebook_path = analysis_dir / "market_products_analysis.ipynb"

nb = nbf.v4.new_notebook()
nb["metadata"]["kernelspec"] = {
    "display_name": "Python 3",
    "language": "python",
    "name": "python3",
}
nb["metadata"]["language_info"] = {"name": "python", "version": "3.11"}

nb["cells"] = [
    nbf.v4.new_markdown_cell(
        """# 중고거래 상품 데이터 전처리 및 탐색 분석

## tl;dr

- 중복 제거 후 통합 데이터는 **11,550건**, 중고나라 데이터는 **3,561건**입니다.
- 중고나라 3,561건은 모두 통합 데이터에 포함되어 있으므로 두 파일을 단순 결합하면 중복 집계됩니다.
- 분석 기준 파일은 `market_products_analysis_dataset.csv`이며, 기준 당근 파일과 같은 21개 컬럼에 분석용 `플랫폼` 컬럼을 추가했습니다.
- 가격 중앙값은 통합 데이터 **79,000원**, 중고나라 **90,000원**입니다. 다만 부품 및 가격 불확실 행을 제외한 비교는 아래 셀에서 별도로 확인합니다.
- 지역의 `구`를 확정하지 못한 비율은 통합 49.32%, 중고나라 97.08%여서 구 단위 분석은 `미상`을 분리해야 합니다.
"""
    ),
    nbf.v4.new_markdown_cell(
        """## Context & Methods

### Key Assumptions

- 한 행은 하나의 판매 게시물입니다.
- 기준 당근 CSV와 15개 원천 컬럼이 정확히 같은 행은 기준 파일의 파생값을 재사용했습니다.
- 나머지 행은 제목·설명·지역의 키워드로 `구`, `완제품여부`, `세부유형`을 보완했습니다.
- 가격 분석은 원화 정수형 `가격원`을 사용하며, `완제품여부=완제품` 및 `가격신뢰=신뢰`를 기본 유효 표본으로 봅니다.
- 결과는 기술·탐색 분석이며 인과관계를 뜻하지 않습니다.
"""
    ),
    nbf.v4.new_code_cell(
        """from pathlib import Path
import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

ANALYSIS_DIR = Path.cwd()
OUTPUT_DIR = ANALYSIS_DIR / "output"
DATA_PATH = OUTPUT_DIR / "market_products_analysis_dataset.csv"
SUMMARY_PATH = OUTPUT_DIR / "quality_summary.json"

pd.set_option("display.max_columns", 30)
sns.set_theme(style="whitegrid", font_scale=0.95)
data = pd.read_csv(DATA_PATH, encoding="utf-8-sig")
summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
data.shape, data.head(3)"""
    ),
    nbf.v4.new_markdown_cell("## Data\n\n### 1. 스키마와 품질 요약"),
    nbf.v4.new_code_cell(
        """quality = pd.DataFrame(summary["quality"])
quality[[
    "dataset", "input_rows", "output_rows", "exact_duplicates_removed",
    "reference_matches", "reference_match_rate_pct", "district_unknown_rate_pct",
    "price_missing", "price_median_won", "component_rows", "approximate_date_rows"
]]"""
    ),
    nbf.v4.new_code_cell(
        """required_columns = [
    "플랫폼", "구", "카테고리", "검색어", "제목", "상태", "가격", "지역", "등록시각",
    "채팅수", "관심수", "조회수", "매너온도", "판매자닉네임", "상세카테고리",
    "거래희망장소", "상세설명", "가격원", "완제품여부", "세부유형", "가격신뢰", "날짜정밀도"
]
assert list(data.columns) == required_columns
assert data.duplicated(subset=required_columns[2:17]).sum() == 0
assert set(data["가격신뢰"].dropna().unique()) <= {"신뢰", "불확실", "해당없음"}
assert set(data["완제품여부"].dropna().unique()) <= {"완제품", "부품"}
print("스키마·중복·분류값 검증 통과")"""
    ),
    nbf.v4.new_markdown_cell("## Results\n\n### 2. 플랫폼 구성과 가격 분포"),
    nbf.v4.new_code_cell(
        """platform_counts = data["플랫폼"].value_counts().rename_axis("플랫폼").reset_index(name="게시물수")
valid_price = data[
    data["가격원"].notna()
    & data["가격신뢰"].eq("신뢰")
    & data["완제품여부"].eq("완제품")
].copy()
platform_price = (
    valid_price.groupby("플랫폼")["가격원"]
    .agg(표본수="size", 중앙값="median", 평균="mean", 하위25="quantile")
)
platform_price["하위25"] = valid_price.groupby("플랫폼")["가격원"].quantile(0.25)
platform_price["상위75"] = valid_price.groupby("플랫폼")["가격원"].quantile(0.75)
platform_counts, platform_price.round(0).astype(int)"""
    ),
    nbf.v4.new_code_cell(
        """fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
sns.barplot(data=platform_counts, x="플랫폼", y="게시물수", ax=axes[0], color="#4C78A8")
axes[0].set_title("플랫폼별 게시물 수")
axes[0].set_xlabel("")

price_plot = platform_price.reset_index()
sns.barplot(data=price_plot, x="플랫폼", y="중앙값", ax=axes[1], color="#F58518")
axes[1].set_title("플랫폼별 유효 완제품 가격 중앙값")
axes[1].set_xlabel("")
axes[1].set_ylabel("원")
plt.tight_layout()
plt.show()"""
    ),
    nbf.v4.new_markdown_cell("### 3. 카테고리별 규모와 가격"),
    nbf.v4.new_code_cell(
        """category_summary = (
    valid_price.groupby("카테고리")
    .agg(게시물수=("가격원", "size"), 가격중앙값=("가격원", "median"), 가격평균=("가격원", "mean"))
    .sort_values("게시물수", ascending=False)
)
category_summary.round(0).astype(int)"""
    ),
    nbf.v4.new_code_cell(
        """plot_data = category_summary.reset_index().sort_values("게시물수", ascending=True)
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
sns.barplot(data=plot_data, y="카테고리", x="게시물수", ax=axes[0], color="#4C78A8")
axes[0].set_title("카테고리별 유효 게시물 수")
sns.barplot(data=plot_data, y="카테고리", x="가격중앙값", ax=axes[1], color="#54A24B")
+axes[1].set_title("카테고리별 유효 가격 중앙값")
axes[1].set_xlabel("원")
plt.tight_layout()
plt.show()""".replace("\n+axes", "\naxes")
    ),
    nbf.v4.new_markdown_cell("### 4. 거래 상태와 참여 지표"),
    nbf.v4.new_code_cell(
        """status_share = pd.crosstab(data["플랫폼"], data["상태"], normalize="index").mul(100).round(1)
engagement_corr = data[["가격원", "채팅수", "관심수", "조회수"]].corr(method="spearman").round(3)
display(status_share)
display(engagement_corr)"""
    ),
    nbf.v4.new_markdown_cell(
        """## Takeaways

- 통합 파일이 이미 중고나라 파일 전체를 포함하므로 전체 시장 분석은 통합 파일만 사용해야 합니다.
- 가격 비교에서는 부품과 가격 불확실 행을 제외하고 중앙값과 사분위 범위를 함께 보세요. 평균은 고가 이상치에 민감합니다.
- 중고나라 행은 구 정보가 대부분 확정되지 않으므로 구별 비교 결과에 포함시키면 지역 편향이 생길 수 있습니다.
- 플랫폼별 채팅·관심·조회 지표는 수집 정책이 서로 다를 수 있어 절대값 비교보다 플랫폼 내부 순위나 상관관계 분석에 적합합니다.
- 다음 분석 단계로는 동일 카테고리·세부유형 내 플랫폼 가격 차이, 판매상태별 가격 차이, 조회 대비 관심/채팅 전환율을 권장합니다.
"""
    ),
]

nbf.write(nb, notebook_path)
print(notebook_path)
