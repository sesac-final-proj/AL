from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BACK_DATA_DIR = ROOT / "back" / "data"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

INTEGRATED_PATH = ROOT / "integrated_market_products.csv"
JOONGGONARA_PATH = BACK_DATA_DIR / "joonggonara_conformed.csv" if (BACK_DATA_DIR / "joonggonara_conformed.csv").exists() else ROOT / "joonggonara_conformed.csv"
ELECMART_PATH = BACK_DATA_DIR / "elecmart_conformed.csv" if (BACK_DATA_DIR / "elecmart_conformed.csv").exists() else ROOT / "elecmart_conformed.csv"

REFERENCE_GLOB = "daangn*.csv"

BASE_COLUMNS = [
    "카테고리",
    "검색어",
    "제목",
    "상태",
    "가격",
    "지역",
    "등록시각",
    "채팅수",
    "관심수",
    "조회수",
    "매너온도",
    "판매자닉네임",
    "상세카테고리",
    "거래희망장소",
    "상세설명",
]
DERIVED_COLUMNS = ["구", "가격원", "완제품여부", "세부유형", "가격신뢰", "날짜정밀도"]
OUTPUT_COLUMNS = ["구", *BASE_COLUMNS, *DERIVED_COLUMNS[1:]]

SEOUL_DISTRICTS = [
    "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
    "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
    "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구", "중랑구",
]

PART_PATTERN = re.compile(
    r"부품용|부품만|본체만|고장|작동안|작동\s*불|수리용|배터리\s*교체|밧데리\s*교체|"
    r"충전\s*불|파손|깨짐|크랙|미작동|전원\s*안|하자",
    re.IGNORECASE,
)

SUBTYPE_RULES = [
    # 다이슨 청소기
    (r"V\s*15", "V15"), (r"V\s*12", "V12"), (r"V\s*11", "V11"),
    (r"V\s*10", "V10"), (r"V\s*8", "V8"), (r"V\s*7", "V7"), (r"V\s*6", "V6"),
    (r"워시\s*G?1|Wash\s*G?1", "WashG1"), (r"옴니.?글라이드", "옴니글라이드"),
    (r"펜슬\s*백", "펜슬백"), (r"사[이싸]클론", "사이클론"),
    # 메디큐브 부스터프로
    (r"X\s*2", "X2"), (r"미니\s*플러스", "미니플러스"), (r"미니", "미니"),
    # 쿠쿠 밥솥
    (r"(?:17|십칠)\s*인용", "17인용"), (r"(?:16|십육)\s*인용", "16인용"),
    (r"(?:14|십사)\s*인용", "14인용"), (r"(?:10|십)\s*인용", "10인용"),
    (r"(?:8|팔)\s*인용", "8인용"), (r"(?:6|육)\s*인용", "6인용"),
    (r"(?:5|오)\s*인용", "5인용"), (r"(?:4|사)\s*인용", "4인용"),
    (r"(?:3|삼)\s*인용", "3인용"), (r"(?:2|이)\s*인용", "2인용"),
    (r"(?:1|일)\s*인용", "1인용"), (r"업소용", "업소용"),
    # 풀리오 마사지 기기
    (r"넥풀러", "넥풀러"), (r"종아리", "종아리"), (r"목.?어깨", "목어깨"), (r"어깨", "어깨"),
    (r"마사지\s*건", "마사지건"), (r"손\s*목", "손목"), (r"손\s*마사지", "손"),
    (r"마사지\s*부츠", "마사지부츠"), (r"괄사", "괄사"), (r"허벅지", "허벅지"), (r"다리", "다리"),
    (r"등.?허리", "등허리"), (r"허리", "허리"), (r"마사지\s*매트", "마사지매트"),
    (r"마사지\s*베개", "마사지베개"), (r"두피", "두피"),
]


def normalize_text(value: object) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def load_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    frame.columns = [normalize_text(column) for column in frame.columns]
    missing = [column for column in BASE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name}: 필수 컬럼 누락: {missing}")
    frame = frame[BASE_COLUMNS].copy()
    for column in BASE_COLUMNS:
        frame[column] = frame[column].map(normalize_text)
    return frame


def find_reference_path() -> Path:
    # 1) Direct search in Downloads
    downloads = Path.home() / "Downloads"
    for p in downloads.iterdir():
        norm = unicodedata.normalize("NFC", p.name)
        if "1차전처리완료" in norm and norm.endswith(".csv") and "사본" not in norm:
            return p

    # 2) Fallback glob
    candidates = list(downloads.glob(REFERENCE_GLOB))
    candidates = [path for path in candidates if "1차전처리완료" in unicodedata.normalize("NFC", path.name)]
    if not candidates:
        raise FileNotFoundError("Downloads에서 1차전처리완료 당근 CSV를 찾지 못했습니다.")
    return max(candidates, key=lambda path: path.stat().st_size)


def load_reference(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    frame.columns = [normalize_text(column) for column in frame.columns]
    required = [*BASE_COLUMNS, *DERIVED_COLUMNS]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name}: 기준 컬럼 누락: {missing}")
    for column in required:
        frame[column] = frame[column].map(normalize_text)
    return frame[required].copy()


def make_key(frame: pd.DataFrame) -> pd.Series:
    return frame[BASE_COLUMNS].agg("\x1f".join, axis=1)


def build_reference_maps(reference: pd.DataFrame) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    keyed = reference.assign(_key=make_key(reference)).drop_duplicates("_key", keep="first")
    derived_map = keyed.set_index("_key")[DERIVED_COLUMNS].to_dict(orient="index")

    region_gu = (
        reference.loc[reference["지역"].ne("") & reference["구"].ne(""), ["지역", "구"]]
        .drop_duplicates()
        .groupby("지역")["구"]
        .agg(lambda values: values.iloc[0] if values.nunique() == 1 else "")
    )
    return derived_map, region_gu[region_gu.ne("")].to_dict()


def parse_price(value: str) -> tuple[int | None, str]:
    text = normalize_text(value)
    if re.search(r"무료\s*나눔|나눔", text):
        return 0, "신뢰"
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return None, "불확실"
    amount = int(digits)
    confidence = "불확실" if re.search(r"협의|문의|부터|~|〜|-", text) else "신뢰"
    return amount, confidence


def infer_gu(row: pd.Series, region_gu: dict[str, str]) -> str:
    # 1) Direct lookup in reference region map
    if row["지역"] in region_gu:
        return region_gu[row["지역"]]

    loc = str(row["거래희망장소"])
    # 2) Explicit '서울특별시 <구>' or '서울 <구>' in trade location
    m_seoul = re.search(r"서울(?:특별시)?\s+([가-힣]+구)", loc)
    if m_seoul and m_seoul.group(1) in SEOUL_DISTRICTS:
        return m_seoul.group(1)

    # 3) Non-Seoul province explicitly mentioned at start -> unknown (prevent false matches like 대구 중구, 부산 강서구)
    if re.match(r"^(?:경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|충청|전북|전남|전라|경북|경남|경상|제주)", loc):
        return "미상"

    # 4) Haystack matching for Seoul districts
    haystack = " ".join(str(row[column]) for column in ["지역", "거래희망장소", "제목", "상세설명"])
    matches = [district for district in SEOUL_DISTRICTS if district in haystack]
    return matches[0] if len(set(matches)) == 1 else "미상"


def infer_completion(row: pd.Series) -> str:
    text = f"{row['제목']} {row['상세설명']}"
    return "부품" if PART_PATTERN.search(text) else "완제품"


def infer_subtype(row: pd.Series) -> str:
    kw = str(row["검색어"])
    # Minix and Brezza have empty/NaN subtype in reference dataset
    if "미닉스" in kw or "브레짜" in kw:
        return ""

    text = f"{row['검색어']} {row['제목']} {row['상세설명']}"
    for pattern, label in SUBTYPE_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return label

    if "메디큐브" in kw:
        return "일반"
    if any(k in kw for k in ["다이슨", "쿠쿠", "풀리오"]):
        return "기타"
    return "일반" if row["카테고리"] else "기타"


def infer_date_precision(value: str) -> str:
    return "정밀" if re.fullmatch(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", value) else "대략"


def conform(frame: pd.DataFrame, reference_map: dict[str, dict[str, str]], region_gu: dict[str, str]) -> tuple[pd.DataFrame, dict[str, int]]:
    input_rows = len(frame)
    exact_duplicates = int(frame.duplicated(subset=BASE_COLUMNS).sum())
    result = frame.drop_duplicates(subset=BASE_COLUMNS, keep="first").reset_index(drop=True)
    keys = make_key(result)
    matched = keys.isin(reference_map)

    derived_records = []
    for (_, row), key in zip(result.iterrows(), keys):
        if key in reference_map:
            derived_records.append(reference_map[key])
            continue
        price_won, price_confidence = parse_price(row["가격"])
        completion = infer_completion(row)
        derived_records.append({
            "구": infer_gu(row, region_gu),
            "가격원": "" if price_won is None else str(price_won),
            "완제품여부": completion,
            "세부유형": infer_subtype(row),
            "가격신뢰": "해당없음" if completion == "부품" else price_confidence,
            "날짜정밀도": infer_date_precision(row["등록시각"]),
        })
    derived = pd.DataFrame(derived_records, columns=DERIVED_COLUMNS)
    result = pd.concat([result, derived], axis=1)[OUTPUT_COLUMNS]
    result["가격원"] = pd.to_numeric(result["가격원"], errors="coerce").astype("Int64")
    for column in ["채팅수", "관심수", "조회수"]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0).astype("Int64")
    result["매너온도"] = pd.to_numeric(result["매너온도"].str.replace("℃", "", regex=False), errors="coerce")

    stats = {
        "input_rows": input_rows,
        "output_rows": len(result),
        "exact_duplicates_removed": exact_duplicates,
        "reference_matches": int(matched.sum()),
        "reference_match_rate_pct": round(float(matched.mean() * 100), 2),
    }
    return result, stats


def infer_platform(frame: pd.DataFrame) -> pd.Series:
    details = frame["상세설명"].str.lower()
    return pd.Series(
        np.select(
            [details.str.contains("web.joongna.com", regex=False), details.str.contains("m.bunjang.co.kr", regex=False)],
            ["중고나라", "번개장터"],
            default="당근/기타",
        ),
        index=frame.index,
        dtype="string",
    )


def summarize(name: str, frame: pd.DataFrame, stats: dict[str, int]) -> dict[str, object]:
    valid_price = frame["가격원"].dropna()
    return {
        "dataset": name,
        **stats,
        "district_unknown": int(frame["구"].eq("미상").sum()),
        "district_unknown_rate_pct": round(float(frame["구"].eq("미상").mean() * 100), 2),
        "price_missing": int(frame["가격원"].isna().sum()),
        "price_median_won": None if valid_price.empty else int(valid_price.median()),
        "price_p25_won": None if valid_price.empty else int(valid_price.quantile(0.25)),
        "price_p75_won": None if valid_price.empty else int(valid_price.quantile(0.75)),
        "component_rows": int(frame["완제품여부"].eq("부품").sum()),
        "approximate_date_rows": int(frame["날짜정밀도"].eq("대략").sum()),
        "status_counts": {str(k): int(v) for k, v in frame["상태"].value_counts().items()},
        "category_counts": {str(k): int(v) for k, v in frame["카테고리"].value_counts().items()},
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BACK_DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("기준 파일 로딩 중...")
    reference_path = find_reference_path()
    print(f"기준 파일: {unicodedata.normalize('NFC', str(reference_path))}")
    reference = load_reference(reference_path)
    reference_map, region_gu = build_reference_maps(reference)

    # 1) Joonggonara preprocessing
    print("\n중고나라 데이터 전처리 중...")
    joonggonara_raw = load_csv(JOONGGONARA_PATH)
    joonggonara, joonggonara_stats = conform(joonggonara_raw, reference_map, region_gu)
    joonggonara["플랫폼"] = "중고나라"

    # 2) Elecmart (Bunjang) preprocessing
    print("번개장터(Elecmart) 데이터 전처리 중...")
    elecmart_raw = load_csv(ELECMART_PATH)
    elecmart, elecmart_stats = conform(elecmart_raw, reference_map, region_gu)
    elecmart["플랫폼"] = "번개장터"

    # 3) Integrated dataset preprocessing (if exists)
    integrated = None
    integrated_stats = None
    if INTEGRATED_PATH.exists():
        print("통합 데이터셋 전처리 중...")
        integrated_raw = load_csv(INTEGRATED_PATH)
        integrated, integrated_stats = conform(integrated_raw, reference_map, region_gu)
        integrated["플랫폼"] = infer_platform(integrated)

    # Output paths
    joonggonara_out = OUTPUT_DIR / "joonggonara_analysis_ready.csv"
    elecmart_out = OUTPUT_DIR / "elecmart_analysis_ready.csv"
    back_jg_out = BACK_DATA_DIR / "joonggonara_analysis_ready.csv"
    back_elec_out = BACK_DATA_DIR / "elecmart_analysis_ready.csv"
    back_jg_preprocessed = BACK_DATA_DIR / "joonggonara_1차전처리완료.csv"
    back_elec_preprocessed = BACK_DATA_DIR / "elecmart_1차전처리완료.csv"

    # Save Joonggonara
    joonggonara[OUTPUT_COLUMNS].to_csv(joonggonara_out, index=False, encoding="utf-8-sig")
    joonggonara[OUTPUT_COLUMNS].to_csv(back_jg_out, index=False, encoding="utf-8-sig")
    joonggonara[OUTPUT_COLUMNS].to_csv(back_jg_preprocessed, index=False, encoding="utf-8-sig")

    # Save Elecmart
    elecmart[OUTPUT_COLUMNS].to_csv(elecmart_out, index=False, encoding="utf-8-sig")
    elecmart[OUTPUT_COLUMNS].to_csv(back_elec_out, index=False, encoding="utf-8-sig")
    elecmart[OUTPUT_COLUMNS].to_csv(back_elec_preprocessed, index=False, encoding="utf-8-sig")

    print(f"\n[저장 완료] 중고나라:")
    print(f"  - {joonggonara_out} ({len(joonggonara):,}행)")
    print(f"  - {back_jg_out}")
    print(f"  - {back_jg_preprocessed}")

    print(f"[저장 완료] 번개장터(Elecmart):")
    print(f"  - {elecmart_out} ({len(elecmart):,}행)")
    print(f"  - {back_elec_out}")
    print(f"  - {back_elec_preprocessed}")

    # Save integrated / analysis dataset
    quality_list = [
        summarize("joonggonara", joonggonara, joonggonara_stats),
        summarize("elecmart", elecmart, elecmart_stats),
    ]

    if integrated is not None:
        integrated_out = OUTPUT_DIR / "integrated_market_products_analysis_ready.csv"
        combined_out = OUTPUT_DIR / "market_products_analysis_dataset.csv"
        integrated[OUTPUT_COLUMNS].to_csv(integrated_out, index=False, encoding="utf-8-sig")
        integrated[["플랫폼", *OUTPUT_COLUMNS]].to_csv(combined_out, index=False, encoding="utf-8-sig")
        quality_list.insert(0, summarize("integrated_market_products", integrated, integrated_stats))

    summary = {
        "sources": {
            "joonggonara": str(JOONGGONARA_PATH),
            "elecmart": str(ELECMART_PATH),
            "reference": str(reference_path),
        },
        "outputs": {
            "joonggonara": [str(joonggonara_out), str(back_jg_out), str(back_jg_preprocessed)],
            "elecmart": [str(elecmart_out), str(back_elec_out), str(back_elec_preprocessed)],
        },
        "quality": quality_list,
    }
    (OUTPUT_DIR / "quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n전처리 품질 요약:")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
