"""Reproducible descriptive analysis for yccraw marketplace CSV files.

This script reports only statistics observed in the input CSV files. It does not
make causal or population-level claims.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from scipy.stats import t, ttest_ind

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT = BASE_DIR / "output" / "statistical_analysis_report.md"
LOCAL_JSON = BASE_DIR / "output" / "analysis-data.json"
FRONT_OUTPUT = BASE_DIR.parent / "front" / "public" / "analysis-data.json"
DATA_ROOT = BASE_DIR / "data" / "cleaned" if (BASE_DIR / "data" / "cleaned").exists() else BASE_DIR / "data" / "raw"
PLATFORMS = ("elecmart", "joonggonara")
EXCLUSION_PATTERNS = (
    ("부품/고장품", r"부품|부속|고장난|고장품|부품용|수리용"),
    ("액세서리/소모품", r"필터|브러시|브러쉬|헤드|충전기|리모컨|케이스|거치대|호스|배터리|내솥|커버|홀더|기판|디스플레이"),
    ("장난감/소품", r"장난감|인형|키즈|소품|사은품|스톤웨어|내열냄비"),
)


def exclusion_reason(title: str) -> str | None:
    for reason, pattern in EXCLUSION_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return reason
    return None


def extract_model(title: str, product: str) -> str:
    if product == "다이슨 청소기":
        match = re.search(r"\b(?:V|DC|SV)\s*-?\s*\d+[A-Z0-9-]*", title, re.IGNORECASE)
    elif product == "쿠쿠 밥솥":
        match = re.search(r"\b(?:CRP|CRH|CPC|CJS|JHR|LHTR|CWF|CWS|EHS|QS)\s*-?\s*[A-Z0-9-]{3,}", title, re.IGNORECASE)
    else:
        match = re.search(r"\b[A-Z]{2,6}\s*-?\s*[A-Z0-9]{3,}(?:-[A-Z0-9]+)*\b", title)
    return re.sub(r"\s+", "", match.group(0)).upper() if match else "모델 미상"


def parse_number(value: str) -> float | None:
    cleaned = "".join(character for character in (value or "") if character.isdigit() or character == ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = mean(values)
    assert average is not None
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


def trimmed_mean(values: list[float], proportion: float = 0.1) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    trim_count = math.floor(len(ordered) * proportion)
    trimmed = ordered[trim_count:len(ordered) - trim_count] if trim_count else ordered
    return mean(trimmed)


def mean_confidence_interval(values: list[float]) -> list[float] | None:
    standard_deviation = sample_std(values)
    if len(values) < 2 or standard_deviation is None:
        return None
    margin = t.ppf(0.975, len(values) - 1) * standard_deviation / math.sqrt(len(values))
    average = mean(values)
    assert average is not None
    return [round(average - margin, 2), round(average + margin, 2)]


def rank(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for position in range(index, end):
            ranks[indexed[position][0]] = average_rank
        index = end
    return ranks


def pearson(values_x: list[float], values_y: list[float]) -> float | None:
    if len(values_x) < 3 or len(values_x) != len(values_y):
        return None
    average_x = mean(values_x)
    average_y = mean(values_y)
    assert average_x is not None and average_y is not None
    centered_x = [value - average_x for value in values_x]
    centered_y = [value - average_y for value in values_y]
    denominator = math.sqrt(sum(value * value for value in centered_x) * sum(value * value for value in centered_y))
    if denominator == 0:
        return None
    return sum(x_value * y_value for x_value, y_value in zip(centered_x, centered_y)) / denominator


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    values_x = [pair[0] for pair in pairs]
    values_y = [pair[1] for pair in pairs]
    return pearson(rank(values_x), rank(values_y))


def read_rows() -> tuple[list[dict[str, str]], Counter[str]]:
    rows: list[dict[str, str]] = []
    excluded: Counter[str] = Counter()
    for platform in PLATFORMS:
        for path in sorted((DATA_ROOT / platform).glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    row["_platform"] = platform
                    row["_file"] = path.name
                    row["_product"] = row.get("search_keyword", "").strip() or "미기재"
                    is_raw = (DATA_ROOT == BASE_DIR / "data" / "raw")
                    if is_raw:
                        reason = exclusion_reason(row.get("title", ""))
                        if reason:
                            excluded[reason] += 1
                            continue
                    row["_price_value"] = parse_number(row.get("price", ""))
                    if is_raw and row["_product"] == "쿠쿠 밥솥" and row["_price_value"] is not None and row["_price_value"] >= 4_000_000:
                        excluded["가격 이상치(쿠쿠 400만원 이상)"] += 1
                        continue
                    row["_model"] = row.get("model", "").strip() or extract_model(row.get("title", ""), row["_product"])
                    row["_product_group"] = f"{row['_product']} / {row['_model']}"
                    row["_status"] = row.get("status", "").strip() or "미기재"
                    row["_observed_at"] = parse_datetime(row.get("update_time") or row.get("registered_at", ""))
                    rows.append(row)
    rejected_path = DATA_ROOT / "rejected_rows.csv"
    if rejected_path.exists():
        with rejected_path.open("r", encoding="utf-8-sig", newline="") as handle:
            excluded.update(row.get("rejection_reason", "미분류") for row in csv.DictReader(handle))
    return rows, excluded


def fmt(value: float | None, decimals: int = 2) -> str:
    return "N/A" if value is None else f"{value:,.{decimals}f}"


def describe_prices(rows: list[dict[str, str]]) -> str:
    prices = [value for row in rows if (value := parse_number(row.get("price", ""))) is not None and value >= 0]
    return (
        f"n={len(prices)}, 평균={fmt(mean(prices), 0)}원, 중앙값={fmt(percentile(prices, 0.5), 0)}원, "
        f"표준편차={fmt(sample_std(prices), 0)}원, Q1={fmt(percentile(prices, 0.25), 0)}원, "
        f"Q3={fmt(percentile(prices, 0.75), 0)}원"
    )


def parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def quarter_label(observed_at: datetime | None) -> str | None:
    if observed_at is None:
        return None
    return f"{observed_at.year} Q{(observed_at.month - 1) // 3 + 1}"


def stats_payload(group_rows: list[dict[str, str]]) -> dict[str, int | float | None]:
    prices = [row["_price_value"] for row in group_rows if row["_price_value"] is not None and row["_price_value"] >= 0]
    average = mean(prices)
    median = percentile(prices, 0.5)
    standard_deviation = sample_std(prices)
    return {
        "count": len(prices),
        "mean": round(average, 2) if average is not None else None,
        "trimmedMean": round(trimmed_mean(prices), 2) if prices else None,
        "median": median,
        "q1": percentile(prices, 0.25),
        "q3": percentile(prices, 0.75),
        "standardDeviation": round(standard_deviation, 2) if standard_deviation is not None else None,
        "coefficientOfVariation": round(standard_deviation / average * 100, 2) if standard_deviation is not None and average else None,
        "meanConfidenceInterval95": mean_confidence_interval(prices),
        "meanMedianGapPct": round((average - median) / median * 100, 2) if average is not None and median not in (None, 0) else None,
    }


def comparison_payload(values_x: list[float], values_y: list[float]) -> dict[str, int | float | None]:
    test = ttest_ind(values_x, values_y, equal_var=False) if len(values_x) >= 2 and len(values_y) >= 2 else None
    mean_difference = mean(values_x) - mean(values_y) if values_x and values_y else None
    variance_x = sample_std(values_x) ** 2 if len(values_x) >= 2 and sample_std(values_x) is not None else None
    variance_y = sample_std(values_y) ** 2 if len(values_y) >= 2 and sample_std(values_y) is not None else None
    standard_error = math.sqrt(variance_x / len(values_x) + variance_y / len(values_y)) if variance_x is not None and variance_y is not None else None
    if standard_error is not None and test is not None:
        degrees_of_freedom = (variance_x / len(values_x) + variance_y / len(values_y)) ** 2 / ((variance_x / len(values_x)) ** 2 / (len(values_x) - 1) + (variance_y / len(values_y)) ** 2 / (len(values_y) - 1))
        margin = t.ppf(0.975, degrees_of_freedom) * standard_error
        confidence_interval = [round(mean_difference - margin, 2), round(mean_difference + margin, 2)]
    else:
        confidence_interval = None
    pooled_sd = math.sqrt(((len(values_x) - 1) * variance_x + (len(values_y) - 1) * variance_y) / (len(values_x) + len(values_y) - 2)) if variance_x is not None and variance_y is not None else None
    effect_size = mean_difference / pooled_sd if mean_difference is not None and pooled_sd else None
    return {
        "elecmartCount": len(values_x), "joonggonaraCount": len(values_y),
        "meanDifference": round(mean_difference, 2) if mean_difference is not None else None,
        "confidenceInterval95": confidence_interval,
        "tStatistic": round(float(test.statistic), 4) if test is not None else None,
        "pValue": round(float(test.pvalue), 4) if test is not None else None,
        "cohensD": round(effect_size, 4) if effect_size is not None else None,
    }


def build_front_payload(rows: list[dict[str, str]]) -> dict:
    platforms = []
    for platform in PLATFORMS:
        platform_rows = [row for row in rows if row["_platform"] == platform]
        ids = [row.get("product_id", "").strip() for row in platform_rows if row.get("product_id", "").strip()]
        statuses = Counter(row["_status"] for row in platform_rows)
        prices = [row["_price_value"] for row in platform_rows if row["_price_value"] is not None and row["_price_value"] >= 0]
        price_stats = stats_payload(platform_rows)
        platforms.append({
            "name": platform,
            "rows": len(platform_rows),
            "meanPrice": price_stats["mean"],
            "trimmedMeanPrice": price_stats["trimmedMean"],
            "medianPrice": price_stats["median"],
            "standardDeviation": price_stats["standardDeviation"],
            "coefficientOfVariation": price_stats["coefficientOfVariation"],
            "meanConfidenceInterval95": price_stats["meanConfidenceInterval95"],
            "meanMedianGapPct": price_stats["meanMedianGapPct"],
            "duplicateRate": round((len(ids) - len(set(ids))) / len(ids) * 100, 2) if ids else None,
            "statuses": dict(statuses),
        })

    product_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        product_groups[row["_product_group"]].append(row)
    products = []
    for product, product_rows in sorted(product_groups.items()):
        item = product.split(" / ", 1)[0]
        model = product.split(" / ", 1)[1] if " / " in product else "모델 미상"
        products.append({
            "name": product,
            "item": item,
            "model": model,
            **{platform: stats_payload([row for row in product_rows if row["_platform"] == platform]) for platform in PLATFORMS},
        })
        product_item = products[-1]
        platform_prices = {
            platform: [row["_price_value"] for row in product_rows if row["_platform"] == platform and row["_price_value"] is not None and row["_price_value"] >= 0]
            for platform in PLATFORMS
        }
        pooled_prices = platform_prices["elecmart"] + platform_prices["joonggonara"]
        product_item["referencePrice"] = percentile(pooled_prices, 0.5)
        elecmart_median = product_item["elecmart"]["median"]
        joonggonara_median = product_item["joonggonara"]["median"]
        product_item["elecmartVsJoonggonaraPct"] = round((elecmart_median - joonggonara_median) / joonggonara_median * 100, 2) if elecmart_median is not None and joonggonara_median not in (None, 0) else None

    quarter_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        label = quarter_label(row["_observed_at"])
        if label:
            quarter_groups[(label, row["_platform"], row["_product_group"])].append(row)
    quarters = []
    for (label, platform, product), group_rows in sorted(quarter_groups.items()):
        stats = stats_payload(group_rows)
        statuses = Counter(row["_status"] for row in group_rows)
        quarters.append({
            "label": label,
            "platform": platform,
            "product": product,
            "item": product.split(" / ", 1)[0],
            "model": product.split(" / ", 1)[1] if " / " in product else "모델 미상",
            "count": stats["count"],
            "mean": stats["mean"],
            "median": stats["median"],
            "selling": statuses.get("판매중", 0),
            "reserved": statuses.get("예약중", 0),
            "sold": statuses.get("판매완료", 0),
        })

    elecmart_rows = [row for row in rows if row["_platform"] == "elecmart"]
    correlations = {}
    for name, field in (("favorites", "num_faved"), ("comments", "num_comment")):
        pairs = [(row["_price_value"], parse_number(row.get(field, ""))) for row in elecmart_rows]
        valid_pairs = [(price, metric) for price, metric in pairs if price is not None and metric is not None and price >= 0 and metric >= 0]
        correlations[name] = {"count": len(valid_pairs), "rho": spearman(valid_pairs)}
    comparisons = []
    for item in sorted({product["item"] for product in products}):
        item_rows = [row for row in rows if row["_product"] == item]
        prices_by_platform = {
            platform: [row["_price_value"] for row in item_rows if row["_platform"] == platform and row["_price_value"] is not None and row["_price_value"] >= 0]
            for platform in PLATFORMS
        }
        elecmart_median = percentile(prices_by_platform["elecmart"], 0.5)
        joonggonara_median = percentile(prices_by_platform["joonggonara"], 0.5)
        comparison_stats = comparison_payload(prices_by_platform["elecmart"], prices_by_platform["joonggonara"])
        comparisons.append({
            "item": item,
            **comparison_stats,
            "elecmartMean": round(mean(prices_by_platform["elecmart"]), 2) if prices_by_platform["elecmart"] else None,
            "joonggonaraMean": round(mean(prices_by_platform["joonggonara"]), 2) if prices_by_platform["joonggonara"] else None,
            "elecmartTrimmedMean": round(trimmed_mean(prices_by_platform["elecmart"]), 2) if prices_by_platform["elecmart"] else None,
            "joonggonaraTrimmedMean": round(trimmed_mean(prices_by_platform["joonggonara"]), 2) if prices_by_platform["joonggonara"] else None,
            "elecmartStandardDeviation": round(sample_std(prices_by_platform["elecmart"]), 2) if len(prices_by_platform["elecmart"]) >= 2 else None,
            "joonggonaraStandardDeviation": round(sample_std(prices_by_platform["joonggonara"]), 2) if len(prices_by_platform["joonggonara"]) >= 2 else None,
            "elecmartMedian": elecmart_median,
            "joonggonaraMedian": joonggonara_median,
            "medianDifference": elecmart_median - joonggonara_median if elecmart_median is not None and joonggonara_median is not None else None,
            "elecmartPremiumPct": round((elecmart_median - joonggonara_median) / joonggonara_median * 100, 2) if elecmart_median is not None and joonggonara_median not in (None, 0) else None,
        })
    candidates = [(product["name"], platform, product[platform]["mean"]) for product in products for platform in PLATFORMS if product[platform]["mean"] is not None and product[platform]["count"] >= 10]
    expensive = max(candidates, key=lambda candidate: candidate[2]) if candidates else (None, None, None)
    observed_dates = [row["_observed_at"] for row in rows if row["_observed_at"] is not None]
    overall_prices = {
        platform: [row["_price_value"] for row in rows if row["_platform"] == platform and row["_price_value"] is not None and row["_price_value"] >= 0]
        for platform in PLATFORMS
    }
    return {"rowCount": len(rows), "fileCount": len({(row["_platform"], row["_file"]) for row in rows}), "overallStats": stats_payload(rows), "overallComparison": comparison_payload(overall_prices["elecmart"], overall_prices["joonggonara"]), "platforms": platforms, "products": products, "quarters": quarters, "comparison": comparisons, "expensive": {"product": expensive[0], "platform": expensive[1], "mean": expensive[2]}, "correlations": correlations, "coverage": {"datedRowCount": len(observed_dates), "datedRowRate": round(len(observed_dates) / len(rows) * 100, 2) if rows else 0, "from": min(observed_dates).date().isoformat() if observed_dates else None, "to": max(observed_dates).date().isoformat() if observed_dates else None}}


def build_report(rows: list[dict[str, str]], excluded: Counter[str]) -> str:
    file_count = len({(row["_platform"], row["_file"]) for row in rows})
    lines = [
        "# 시장 데이터 통계 분석 보고서",
        "",
        "> 이 보고서는 수집된 마켓 CSV에 포함된 관측값만 기술합니다. 표본 밖 시장 전체에 대한 추정이나 인과관계 해석은 포함하지 않습니다.",
        "",
        "## 데이터 범위",
        "",
        f"- 분석 파일 수: {file_count}개",
        f"- 분석 행 수: {len(rows):,}개",
        f"- 플랫폼: {', '.join(PLATFORMS)}",
        "- 가격 통계는 `price`를 숫자로 변환할 수 있고 0 이상인 행만 사용했습니다.",
        "- 중복은 플랫폼별 `product_id`가 같은 행으로 계산했습니다.",
        f"- 전처리 제외 행: {sum(excluded.values()):,}건 ({', '.join(f'{reason} {count}건' for reason, count in sorted(excluded.items()))})",
        "- 제외 기준: 상세설명을 포함한 부품·고장품·장난감·타제품·불완전상품 판정과 가격 이상치의 문맥 증거를 사용했습니다.",
        "- 모델 기준: 제목과 수집 가능한 상세설명에서 모델번호를 우선 추출하고, 제품 변형명 또는 `제품군 공통`으로 보완했습니다.",
        "",
        "## 플랫폼별 요약",
        "",
        "| 플랫폼 | 행 수 | 가격 유효 행 | 평균 가격 | 중앙값 | 상태 분포 | product_id 중복률 |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    for platform in PLATFORMS:
        platform_rows = [row for row in rows if row["_platform"] == platform]
        prices = [value for row in platform_rows if (value := parse_number(row.get("price", ""))) is not None and value >= 0]
        statuses = Counter(row.get("status", "").strip() or "미기재" for row in platform_rows)
        ids = [row.get("product_id", "").strip() for row in platform_rows if row.get("product_id", "").strip()]
        duplicate_rows = len(ids) - len(set(ids))
        duplicate_rate = duplicate_rows / len(ids) * 100 if ids else None
        status_text = ", ".join(f"{status} {count}건" for status, count in sorted(statuses.items()))
        lines.append(
            f"| {platform} | {len(platform_rows):,} | {len(prices):,} | {fmt(mean(prices), 0)}원 | "
            f"{fmt(percentile(prices, 0.5), 0)}원 | {status_text} | {fmt(duplicate_rate, 2)}% |"
        )

    lines.extend(["", "## 검색어별 가격 분포", "", "| 플랫폼 | 검색어 | 가격 통계 |", "|---|---|---|"])
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["_platform"], row["_product_group"])].append(row)
    for (platform, keyword), group_rows in sorted(groups.items()):
        lines.append(f"| {platform} | {keyword} | {describe_prices(group_rows)} |")

    quarter_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        quarter = quarter_label(row["_observed_at"])
        if quarter:
            quarter_groups[(quarter, row["_platform"], row["_product_group"])].append(row)
    lines.extend(["", "## 모델별 분기 지표", "", "| 분기 | 상품 / 모델 | 플랫폼 | 표본 | 평균 가격 | 중앙값 | 판매중 | 예약중 | 판매완료 |", "|---|---|---|---:|---:|---:|---:|---:|---:|"])
    for (quarter, platform, product), quarter_rows in sorted(quarter_groups.items()):
        prices = [value for row in quarter_rows if (value := parse_number(row.get("price", ""))) is not None and value >= 0]
        statuses = Counter(row.get("status", "").strip() or "미기재" for row in quarter_rows)
        lines.append(
            f"| {quarter} | {product} | {platform} | {len(quarter_rows):,} | {fmt(mean(prices), 0)}원 | {fmt(percentile(prices, 0.5), 0)}원 | "
            f"{statuses.get('판매중', 0):,} | {statuses.get('예약중', 0):,} | {statuses.get('판매완료', 0):,} |"
        )

    elecmart_rows = [row for row in rows if row["_platform"] == "elecmart"]
    lines.extend(["", "## 일렉트로마트 참여 지표와 가격의 순위상관", ""])
    lines.append("| 지표 | 유효 쌍 수 | Spearman rho |")
    lines.append("|---|---:|---:|")
    for label, field in (("찜 수", "num_faved"), ("댓글 수", "num_comment")):
        pairs = []
        for row in elecmart_rows:
            price = parse_number(row.get("price", ""))
            metric = parse_number(row.get(field, ""))
            if price is not None and metric is not None and price >= 0 and metric >= 0:
                pairs.append((price, metric))
        lines.append(f"| 가격- {label} | {len(pairs):,} | {fmt(spearman(pairs), 4)} |")
    payload = build_front_payload(rows)
    lines.extend(["", "## 참고 적정가 및 플랫폼 비교", "", "- 참고 적정가는 모델별 양 플랫폼 관측 중앙값입니다. 미래 가격 예측이나 공식 적정가가 아닙니다.", "", "| 품목 | 일렉트로마트 중앙값 | 중고나라 중앙값 | 일렉트로마트 차이 | t 통계량 | p-value |", "|---|---:|---:|---:|---:|---:|"])
    for item in payload["comparison"]:
        lines.append(
            f"| {item['item']} | {fmt(item['elecmartMedian'], 0)}원 | {fmt(item['joonggonaraMedian'], 0)}원 | "
            f"{fmt(item['elecmartPremiumPct'], 2)}% | {fmt(item['tStatistic'], 4)} | {fmt(item['pValue'], 4)} |"
        )
    lines.extend([
        "",
        "해석 기준: rho는 이 표본에서 두 변수의 순위가 함께 움직이는 정도를 나타내는 기술통계입니다. 표본 크기와 수집 방식 때문에 인과관계 또는 전체 시장의 관계로 해석하지 않습니다.",
        "",
        "## 재현 방법",
        "",
        "```text",
        "python crawler/market_analysis/scripts/statistical_analysis.py",
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    rows, excluded = read_rows()
    report = build_report(rows, excluded)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")
    payload = build_front_payload(rows)
    quality_summary_path = DATA_ROOT / "quality_summary.json"
    if quality_summary_path.exists():
        quality_summary = json.loads(quality_summary_path.read_text(encoding="utf-8"))
        payload["preprocessing"] = {
            "rawRowCount": quality_summary["raw_rows"],
            "excludedRowCount": quality_summary["rejected_rows"],
            "excludedByReason": quality_summary["rejected_by_reason"],
            "modelFallback": "제품군 공통",
            "unknownAfter": quality_summary["unknown_after"],
            "detailSuccess": quality_summary["detail_success"],
        }
    else:
        payload["preprocessing"] = {"rawRowCount": len(rows) + sum(excluded.values()), "excludedRowCount": sum(excluded.values()), "excludedByReason": dict(excluded), "rules": [reason for reason, _ in EXCLUSION_PATTERNS], "modelFallback": "모델 미상"}
    LOCAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if FRONT_OUTPUT.parent.exists():
        FRONT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    file_count = len({(row["_platform"], row["_file"]) for row in rows})
    print(f"분석 완료: {len(rows):,}행, {file_count}개 파일, 제외 {sum(excluded.values()):,}행")
    print(f"보고서: {OUTPUT}")
    print(f"JSON: {LOCAL_JSON}")


if __name__ == "__main__":
    main()
