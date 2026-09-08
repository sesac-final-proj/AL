"""Clean Bunjang and Joonggonara listing CSVs for comparable-price analysis.

Raw crawler files stay untouched. Cleaned rows, rejected rows, price-anomaly
reviews, detail-fetch cache, and a quality report are written under
``yccraw/scratch/cleaned``.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_ROOT = BASE_DIR / "data" / "raw"
OUTPUT_ROOT = BASE_DIR / "data" / "cleaned"
PLATFORMS = ("elecmart", "joonggonara")

STRONG_REJECT_RULES = (
    ("부품/고장품", r"부품용|부품만|고장품|고장난|고장났|작동\s*불가|전원\s*(?:불량|안\s*켜)|수리용|파손품|폐제품"),
    ("장난감/모형", r"장난감|미니어처|피규어|인형|모형(?:만|제품)?|키링"),
    ("거래 목적 불일치", r"(?:^|[\s\[(])삽니다|구해요|구합니다|매입합니다|출장\s*매입|렌탈|대여(?:합니다|해요)?"),
    ("불완전상품", r"본체만|기기\s*없|제품\s*없|본품\s*없"),
    ("빈 포장", r"빈\s*박스|공박스|설명서만|박스만\s*(?:판매|팝니다|드려요|있습니다|있어요|구매)"),
)

ACCESSORY_TERMS = {
    "다이슨 청소기": r"헤드|브러시|브러쉬|배터리|충전기|거치대|스탠드|완드|연결봉|호스|필터|먼지통|어댑터|아답터|툴|노즐|청소봉|파이프",
    "쿠쿠 밥솥": r"내솥|패킹|고무패킹|뚜껑|분리형\s*커버|압력추|증기\s*캡|전원선|코드선|기판",
    "메디큐브 부스터 프로": r"거치대|파우치|케이스|충전기|부스팅젤|젤만|앰플만|크림만|마스크팩만",
    "미닉스 음식물처리기": r"필터|활성탄|탈취제|통만|뚜껑|바스켓|건조통|내통",
    "브레짜 분유": r"깔때기|깔대기|물통|분유통|부품|스페어|필터|세척제|받침대|트레이",
    "풀리오 마사지기": r"파우치|충전기|어댑터|아답터|리모컨|커버|압박스타킹|밴드만",
}

MAIN_PRODUCT_TERMS = {
    "다이슨 청소기": r"청소기|버큠|vacuum|washg1|워시g1|로봇청소기",
    "쿠쿠 밥솥": r"밥솥|전기솥|보온솥|압력솥",
    "메디큐브 부스터 프로": r"부스터\s*프로|booster\s*pro",
    "미닉스 음식물처리기": r"음식물\s*처리기|더\s*플[렌랜]더",
    "브레짜 분유": r"분유\s*(?:제조기|메이커)|포뮬[러라]|formula\s*pro",
    "풀리오 마사지기": r"마사지기|마사지건|넥풀러|백풀러|풀리지|풀리션",
}

OFF_TARGET_RULES = {
    "다이슨 청소기": r"차이슨|에어랩|슈퍼소닉|드라이기|선풍기|공기청정기|가습기",
    "쿠쿠 밥솥": r"치치쿠쿠|인덕션\s*솥밥기계|에어프라이어|정수기|제빵기|식기세척기",
    "메디큐브 부스터 프로": r"하이포커스샷|울트라튠|에어샷|아이샷|유쎄라|더마\s*ems|브이롤러|전동\s*칫솔",
    "미닉스 음식물처리기": r"풀무원|스마트카라|린클|휴렉|락앤락|루펜|싱크대\s*거름",
    "브레짜 분유": r"브라비|버들맘마|젖병\s*(?:세척기|소독기)|분유\s*포트|보온\s*포트",
    "풀리오 마사지기": r"오아|제스파|코지마|세라젬|바디프랜드|마사지\s*의자",
}

TARGET_BRAND_TERMS = {
    "다이슨 청소기": r"다이슨|DYSON",
    "쿠쿠 밥솥": r"쿠쿠|CUCKOO",
    "메디큐브 부스터 프로": r"메디큐브|MEDICUBE",
    "미닉스 음식물처리기": r"미닉스|MINIX",
    "브레짜 분유": r"브레짜|BREZZA",
    "풀리오 마사지기": r"풀리오|PULIO",
}

PLACEHOLDER_PRICES = {0, 1, 10, 100, 500, 1000, 1234, 12345, 123456, 999999, 1111111}


@dataclass(frozen=True)
class ModelResult:
    value: str
    source: str


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9가-힣]", "", normalized(text).upper())


def code_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", "", match.group(0)).upper()
    return None


def classify_model(text: str, product: str) -> ModelResult:
    source_text = normalized(text)
    dense = compact(source_text)
    if product == "다이슨 청소기":
        code = code_match(source_text, (r"(?:V|SV|DC)\s*-?\s*\d{1,3}[A-Z]?",))
        if code:
            return ModelResult(code.replace("-", ""), "text_code")
        variants = (
            ("360_VIS_NAV", r"360\s*VIS\s*NAV|360비스나브|360\s*비즈"),
            ("GEN5DETECT", r"GEN\s*5\s*DETECT|젠\s*5\s*디텍트"),
            ("DIGITAL_SLIM", r"디지털\s*슬림|DIGITAL\s*SLIM"),
            ("OMNI_GLIDE", r"옴니\s*글라이드|OMNI\s*GLIDE"),
            ("MICRO_1.5KG", r"마이크로\s*1\.?5|MICRO\s*1\.?5"),
            ("PENCILVAC", r"펜슬\s*백|PENCIL\s*VAC"),
            ("WASH_G1", r"워시\s*G1|WASH\s*G1"),
            ("SPOT_AND_SCRUB", r"스팟\s*앤\s*스크럽|SPOT\s*(?:AND|&)\s*SCRUB"),
            ("BIG_BALL", r"빅볼|BIG\s*BALL"),
            ("CYCLONE", r"싸이클론|CYCLONE"),
        )
    elif product == "쿠쿠 밥솥":
        code = code_match(source_text, (
            r"(?:CRP|CRH|CR|CPC|CJS|CWF|CWS)\s*-?\s*[A-Z0-9]{3,}(?:-[A-Z0-9]+)*",
            r"(?:LHTR|NHTR|OHTR|MHTR|JHR|EHS|QS|RT|ST|DHB|BHXB)\s*-?\s*[A-Z0-9]{4,}",
        ))
        if code:
            return ModelResult(code, "text_code")
        capacity = re.search(r"(3|6|10|20|30|35|50)\s*인용", source_text)
        capacity_label = f"_{capacity.group(1)}인용" if capacity else ""
        variants = (
            (f"트윈프레셔{capacity_label}", r"트윈\s*프레셔"),
            (f"마스터셰프{capacity_label}", r"마스터\s*셰프|마스터\s*쉐프"),
            (f"IH압력밥솥{capacity_label}", r"IH\s*(?:전기)?압력"),
            (f"전기압력밥솥{capacity_label}", r"전기\s*압력|압력\s*밥솥"),
            (f"전기보온밥솥{capacity_label}", r"보온\s*밥솥|전기\s*밥솥"),
            (f"미니에그밥솥{capacity_label}", r"미니\s*에그"),
        )
    elif product == "메디큐브 부스터 프로":
        variants = (
            ("BOOSTER_PRO_MINI", r"부스터\s*프로\s*미니|미니\s*부스터\s*프로"),
            ("BOOSTER_PRO", r"부스터\s*프로|부스터프로|프로\s*부스터|프로부스터|BOOSTER\s*PRO"),
        )
    elif product == "미닉스 음식물처리기":
        code = code_match(source_text, (r"(?:MNFD|MAXMNFD|PROMNFD)\s*-?\s*[A-Z0-9]{3,}(?:-[A-Z0-9]+)*",))
        if code:
            return ModelResult(code, "text_code")
        variants = (
            ("THE_PLENDER_MAX", r"더\s*플[렌랜]더\s*MAX|더\s*맥스|\bMAX\b"),
            ("THE_PLENDER_PRO", r"더\s*플[렌랜]더\s*PRO|더\s*프로|\bPRO\b"),
            ("THE_PLENDER", r"더\s*플[렌랜]더|음식물\s*처리기"),
        )
    elif product == "브레짜 분유":
        code = code_match(source_text, (r"BRZFRP\s*-?\s*[12]A",))
        if code:
            return ModelResult(code, "text_code")
        variants = (
            ("FORMULA_PRO_ADVANCED", r"포뮬[러라]\s*프로\s*어드|FORMULA\s*PRO\s*ADV"),
            ("FORMULA_PRO", r"분유\s*제조기|분유\s*메이커|포뮬[러라]\s*프로|FORMULA\s*PRO"),
        )
    elif product == "풀리오 마사지기":
        code = code_match(source_text, (r"(?:PLO|NP|N|PH|ML|HL|HD|CH)\s*-?\s*[A-Z0-9]{3,}",))
        if code:
            return ModelResult(code, "text_code")
        version = re.search(r"(?:^|[^A-Z0-9])V\s*([123])(?:[^A-Z0-9]|$)", source_text, re.IGNORECASE)
        suffix = f"_V{version.group(1)}" if version else ""
        variants = (
            ("NECK_PULLER", r"넥\s*풀러"),
            ("BACK_PULLER", r"백\s*풀러|백\s*플러"),
            ("THIGH_FULLIGE", r"풀리지|허벅지"),
            ("CALF" + suffix, r"종아리|다리\s*마사지"),
            ("NECK_SHOULDER" + suffix, r"목\s*어깨|목어깨|목\s*마사지"),
            ("HAND_WRIST", r"손목|손\s*마사지"),
            ("WAIST_BACK", r"허리|등허리"),
            ("MASSAGE_GUN", r"마사지\s*건"),
            ("SKINFIT", r"스킨\s*핏|스킨핏"),
            ("PULLITION", r"풀리션"),
            ("AIR_GUASHA", r"에어\s*괄사"),
        )
    else:
        variants = ()

    for label, pattern in variants:
        if re.search(pattern, source_text, re.IGNORECASE):
            return ModelResult(label, "text_variant")
    if product and (
        re.search(MAIN_PRODUCT_TERMS.get(product, r"$^"), source_text, re.IGNORECASE)
        or re.search(TARGET_BRAND_TERMS.get(product, r"$^"), source_text, re.IGNORECASE)
    ):
        return ModelResult("제품군 공통", "product_group")
    return ModelResult("모델 미상", "unresolved")


def parse_price(value: Any) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else None


def initial_rejection(text: str, product: str, price: int | None) -> str | None:
    if price is None or price in PLACEHOLDER_PRICES:
        return "가격 무효/자리표시"
    for reason, pattern in STRONG_REJECT_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return reason
    title = normalized(text).split("\n", 1)[0]
    main_pattern = MAIN_PRODUCT_TERMS.get(product)
    off_target = OFF_TARGET_RULES.get(product)
    if off_target and re.search(off_target, title, re.IGNORECASE):
        return "검색어 오탐/타제품"
    target_brand = TARGET_BRAND_TERMS.get(product, r"$^")
    model_from_text = classify_model(text, product)
    if not re.search(target_brand, text, re.IGNORECASE) and model_from_text.source != "text_code":
        return "검색어 오탐/타브랜드"
    accessory_pattern = ACCESSORY_TERMS.get(product)
    if accessory_pattern and re.search(accessory_pattern, title, re.IGNORECASE):
        if not main_pattern or not re.search(main_pattern, title, re.IGNORECASE):
            return "액세서리/소모품 단품"
    off_target_match = re.search(off_target, text, re.IGNORECASE) if off_target else None
    if off_target_match:
        target_match = re.search(TARGET_BRAND_TERMS.get(product, r"$^"), text, re.IGNORECASE)
        if not target_match or off_target_match.start() < target_match.start():
            return "검색어 오탐/타제품"
        if product == "메디큐브 부스터 프로" and not re.search(r"부스터\s*프로", text, re.IGNORECASE):
            return "검색어 오탐/타제품"
    return None


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {str(item["product_id"]): item for item in json.load(handle)}


def save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(cache.values()), ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_bunjang_detail(product_id: str) -> dict[str, Any]:
    url = f"https://api.bunjang.co.kr/api/pms/v1/products/{product_id}/detail/web"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    if response.status_code != 200:
        return {"product_id": product_id, "status": response.status_code, "description": ""}
    product = response.json().get("data", {}).get("product", {})
    return {
        "product_id": product_id,
        "status": 200,
        "title": product.get("name", ""),
        "description": product.get("description", ""),
        "category": (product.get("category") or {}).get("name", ""),
        "quantity": product.get("qty"),
        "condition": product.get("condition", ""),
    }


def fetch_joongna_detail(product_id: str) -> dict[str, Any]:
    url = f"https://web.joongna.com/product/{product_id}"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    if response.status_code != 200:
        return {"product_id": product_id, "status": response.status_code, "description": ""}
    soup = BeautifulSoup(response.text, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and (payload.get("description") or payload.get("name")):
            return {
                "product_id": product_id,
                "status": 200,
                "title": payload.get("name", ""),
                "description": payload.get("description", ""),
            }
    return {"product_id": product_id, "status": 200, "description": ""}


def fetch_details(rows: list[dict[str, str]], platform: str, workers: int) -> dict[str, dict[str, Any]]:
    cache_path = OUTPUT_ROOT / "detail_cache" / f"{platform}.json"
    cache = load_cache(cache_path)
    candidates = []
    anomaly_ids = set()
    anomaly_path = OUTPUT_ROOT / "price_anomaly_review.csv"
    if anomaly_path.exists():
        with anomaly_path.open(encoding="utf-8-sig", newline="") as handle:
            anomaly_ids = {
                row.get("product_id", "") for row in csv.DictReader(handle)
                if row.get("platform") == platform
            }
    for row in rows:
        base_model = classify_model(row.get("title", ""), row.get("search_keyword", ""))
        price = parse_price(row.get("price"))
        needs_detail = base_model.source in {"product_group", "unresolved"}
        suspicious_price = price in PLACEHOLDER_PRICES or (price is not None and price >= 600_000)
        if (needs_detail or suspicious_price or row.get("product_id") in anomaly_ids) and row.get("product_id") not in cache:
            candidates.append(row["product_id"])
    candidates = sorted(set(candidates))
    fetcher = fetch_bunjang_detail if platform == "elecmart" else fetch_joongna_detail
    if candidates:
        print(f"{platform}: detail fetch {len(candidates):,}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetcher, product_id): product_id for product_id in candidates}
        for index, future in enumerate(as_completed(futures), start=1):
            product_id = futures[future]
            try:
                cache[product_id] = future.result()
            except Exception as error:  # network evidence remains explicit in cache
                cache[product_id] = {"product_id": product_id, "status": "error", "error": str(error), "description": ""}
            if index % 100 == 0:
                print(f"{platform}: {index:,}/{len(candidates):,}", flush=True)
                save_cache(cache_path, cache)
    save_cache(cache_path, cache)
    return cache


def percentile(values: list[int], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def price_bounds(rows: list[dict[str, Any]]) -> dict[tuple[str, str], tuple[float, float, float, int]]:
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    product_groups: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        groups[(row["search_keyword"], row["model"])].append(row["_price"])
        product_groups[row["search_keyword"]].append(row["_price"])
    bounds = {}
    for key, values in groups.items():
        reference = values if len(values) >= 8 else product_groups[key[0]]
        median = statistics.median(reference)
        deviations = [abs(value - median) for value in reference]
        mad = statistics.median(deviations)
        q1, q3 = percentile(reference, 0.25), percentile(reference, 0.75)
        iqr = q3 - q1
        lower = max(0.0, min(median - 4.5 * 1.4826 * mad, q1 - 3 * iqr)) if mad or iqr else 0.0
        upper = max(median + 4.5 * 1.4826 * mad, q3 + 3 * iqr) if mad or iqr else median * 4
        bounds[key] = (lower, upper, float(median), len(reference))
    return bounds


def likely_noncomparable_outlier(row: dict[str, Any], lower: float, upper: float) -> str | None:
    text = row["_evidence_text"]
    price = row["_price"]
    if price in PLACEHOLDER_PRICES:
        return "가격 무효/자리표시"
    if price > upper and re.search(r"일괄|세트|묶음|[+＋].*(?:세척기|청소기|마사지기|밥솥|디바이스)|\d+\s*대", text, re.IGNORECASE):
        return "가격 이상치-복수상품"
    if price < lower and re.search(r"부품|본체만|단품|필터|패킹|내솥|헤드|거치대|충전기|빈박스", text, re.IGNORECASE):
        return "가격 이상치-불완전상품"
    return None


def read_platform_rows(platform: str) -> list[dict[str, str]]:
    rows = []
    for path in sorted((INPUT_ROOT / platform).glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row["_source_file"] = path.name
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def process_platform(platform: str, raw_rows: list[dict[str, str]], details: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in raw_rows:
        detail = details.get(str(row.get("product_id", "")), {})
        description = normalized(str(detail.get("description", "")))
        detail_title = normalized(str(detail.get("title", "")))
        evidence_text = "\n".join(value for value in (row.get("title", ""), detail_title, description) if value)
        price = parse_price(row.get("price"))
        reason = initial_rejection(evidence_text, row.get("search_keyword", ""), price)
        model = classify_model(evidence_text, row.get("search_keyword", ""))
        output = {
            **{key: value for key, value in row.items() if not key.startswith("_")},
            "description": description,
            "model": model.value,
            "model_source": ("detail_" if description and model.value != classify_model(row.get("title", ""), row.get("search_keyword", "")).value else "") + model.source,
            "detail_status": detail.get("status", "not_requested"),
            "_platform": platform,
            "_source_file": row["_source_file"],
            "_price": price,
            "_evidence_text": evidence_text,
        }
        if reason:
            rejected.append({**output, "rejection_reason": reason})
        else:
            accepted.append(output)

    bounds = price_bounds(accepted)
    final_rows: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for row in accepted:
        lower, upper, median, reference_count = bounds[(row["search_keyword"], row["model"])]
        is_anomaly = row["_price"] < lower or row["_price"] > upper
        contextual_reason = likely_noncomparable_outlier(row, lower, upper) if is_anomaly else None
        if contextual_reason:
            rejected.append({**row, "rejection_reason": contextual_reason})
            action = "제외"
        else:
            final_rows.append(row)
            action = "유지-검토대상" if is_anomaly else "유지"
        if is_anomaly:
            anomalies.append({
                "platform": platform,
                "source_file": row["_source_file"],
                "product_id": row.get("product_id", ""),
                "search_keyword": row.get("search_keyword", ""),
                "model": row["model"],
                "title": row.get("title", ""),
                "price": row["_price"],
                "reference_median": round(median),
                "robust_lower": round(lower),
                "robust_upper": round(upper),
                "reference_count": reference_count,
                "action": action,
                "reason": contextual_reason or "통계 이상치지만 단일 온전제품 배제 근거 없음",
                "product_url": row.get("product_url", ""),
            })
    return final_rows, rejected, anomalies


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def build_report(summary: dict[str, Any]) -> str:
    lines = [
        "# 중고 플랫폼 재전처리 품질 보고서",
        "",
        "## tl;dr",
        "",
        f"- 원본 {summary['raw_rows']:,}행 중 {summary['accepted_rows']:,}행 유지, {summary['rejected_rows']:,}행 제외.",
        f"- 모델 미상 {summary['unknown_before']:,}행에서 {summary['unknown_after']:,}행으로 감소.",
        f"- 상세 조회 성공 {summary['detail_success']:,}행, 조회 실패·미제공 {summary['detail_failed']:,}행.",
        f"- 가격 이상치 {summary['price_anomalies']:,}행 점검: 근거 있는 비정상 매물만 제외, 단일 온전제품은 감사 파일에 유지.",
        "",
        "## Context & Methods",
        "",
        "- 분석 단위: 플랫폼의 상품 ID별 매물 1건.",
        "- 원본 CSV는 보존. `cleaned` 아래 결과만 분석 입력으로 사용.",
        "- 모델 복구 순서: 제목 모델코드, 상세설명 모델코드, 제품 변형명, 제품군 공통.",
        "- 가격 이상치: 모델 표본 8건 이상이면 모델별, 아니면 품목별 MAD와 3×IQR 병행.",
        "- 통계 이상만으로 삭제하지 않음. 복수상품·부품·자리표시 가격 증거가 함께 있을 때 제외.",
        "",
        "## Results",
        "",
        "| 플랫폼 | 원본 | 유지 | 제외 | 미상 전 | 미상 후 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for platform, item in summary["platforms"].items():
        lines.append(f"| {platform} | {item['raw']:,} | {item['accepted']:,} | {item['rejected']:,} | {item['unknown_before']:,} | {item['unknown_after']:,} |")
    lines.extend(["", "### 제외 사유", "", "| 사유 | 행 수 |", "|---|---:|"])
    for reason, count in sorted(summary["rejected_by_reason"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {reason} | {count:,} |")
    lines.extend([
        "",
        "## Takeaways",
        "",
        "- 모델명 없는 매물을 임의 모델로 만들지 않고 `제품군 공통`으로 분리해 비교 왜곡을 줄임.",
        "- 삭제 행 전체는 `rejected_rows.csv`, 유지된 가격 이상치는 `price_anomaly_review.csv`에서 원문 링크와 함께 검토 가능.",
        "- 중고나라 상세 페이지가 404 또는 차단 응답인 행은 제목만 사용. 상세 미확인 상태를 `detail_status`에 보존.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-details", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    platform_rows = {platform: read_platform_rows(platform) for platform in PLATFORMS}
    detail_caches = {}
    for platform, rows in platform_rows.items():
        cache_path = OUTPUT_ROOT / "detail_cache" / f"{platform}.json"
        detail_caches[platform] = fetch_details(rows, platform, args.workers) if args.fetch_details else load_cache(cache_path)

    all_rejected: list[dict[str, Any]] = []
    all_anomalies: list[dict[str, Any]] = []
    summary_platforms = {}
    unknown_before_total = 0
    unknown_after_total = 0
    accepted_total = 0

    for platform, rows in platform_rows.items():
        cleaned, rejected, anomalies = process_platform(platform, rows, detail_caches[platform])
        base_fields = [key for key in rows[0] if not key.startswith("_")]
        fields = base_fields + [field for field in ("description", "model", "model_source", "detail_status") if field not in base_fields]
        for source_file in sorted({row["_source_file"] for row in cleaned} | {row["_source_file"] for row in rows}):
            file_rows = [public_row(row) for row in cleaned if row["_source_file"] == source_file]
            write_csv(OUTPUT_ROOT / platform / source_file, file_rows, fields)
        unknown_before = sum(classify_model(row.get("title", ""), row.get("search_keyword", "")).value == "모델 미상" for row in rows)
        unknown_after = sum(row["model"] == "모델 미상" for row in cleaned)
        summary_platforms[platform] = {
            "raw": len(rows), "accepted": len(cleaned), "rejected": len(rejected),
            "unknown_before": unknown_before, "unknown_after": unknown_after,
        }
        accepted_total += len(cleaned)
        unknown_before_total += unknown_before
        unknown_after_total += unknown_after
        all_rejected.extend(rejected)
        all_anomalies.extend(anomalies)

    reject_fields = ["platform", "source_file", "product_id", "search_keyword", "model", "title", "price", "rejection_reason", "detail_status", "product_url", "description"]
    reject_rows = [{
        "platform": row["_platform"], "source_file": row["_source_file"],
        **{field: row.get(field, "") for field in reject_fields if field not in {"platform", "source_file"}},
    } for row in all_rejected]
    write_csv(OUTPUT_ROOT / "rejected_rows.csv", reject_rows, reject_fields)
    anomaly_fields = list(all_anomalies[0]) if all_anomalies else ["platform", "product_id", "action"]
    write_csv(OUTPUT_ROOT / "price_anomaly_review.csv", all_anomalies, anomaly_fields)

    cache_items = [item for cache in detail_caches.values() for item in cache.values()]
    summary = {
        "raw_rows": sum(len(rows) for rows in platform_rows.values()),
        "accepted_rows": accepted_total,
        "rejected_rows": len(all_rejected),
        "unknown_before": unknown_before_total,
        "unknown_after": unknown_after_total,
        "detail_success": sum(item.get("status") == 200 and bool(item.get("description")) for item in cache_items),
        "detail_failed": sum(item.get("status") != 200 or not item.get("description") for item in cache_items),
        "price_anomalies": len(all_anomalies),
        "platforms": summary_platforms,
        "rejected_by_reason": dict(Counter(row["rejection_reason"] for row in all_rejected)),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (OUTPUT_ROOT / "quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_ROOT / "quality_report.md").write_text(build_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
