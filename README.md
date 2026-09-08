# 마켓 시세 분석 및 적정가 산출 파이프라인 (Market Price Analysis)

이 디렉토리는 프론트엔드 서비스의 **시세 분석 대시보드(`localhost:3000/analysis`)** 및 **상품 등록 시 참고 적정가 벤치마크(`ListingBenchmark`)** 구현에 필요한 핵심 데이터 수집, 전처리, 통계 분석 및 연동 JSON 생성 파이프라인을 포함합니다.

---

## 1. 개요 및 프론트엔드 연동 구조

```
[원천 마켓 데이터 (CSV)]
  ├── 번개장터 일렉트로마트 (2,206건)
  └── 중고나라 (3,037건)
           │
           ▼
[1단계: marketplace_preprocess.py]
  - 부품/소모품/고장품 필터링
  - 모델명 정규화 및 이상치 정제
           │
           ▼
[2단계: statistical_analysis.py]
  - 기술통계 (평균, 절사평균, 중앙값, 표준편차, 95% CI)
  - Welch's t-검정 & Cohen's d 효과크기
  - 모델별 참고 적정가 산출
           │
           ├──▶ [front/public/analysis-data.json]  ──▶ 프론트엔드 (/analysis 대시보드)
           ├──▶ [output/statistical_analysis_report.md] ──▶ 분석 마크다운 리포트
           └──▶ [notebooks/market_products_analysis.ipynb] ──▶ 시각화 및 검증 노트북
```

---

## 2. 수집 데이터셋 스펙

번개장터(일렉트로마트 원천)와 중고나라에서 크롤링한 6개 주요 가전/전자기기 품목의 총 **5,243건** 표본 데이터를 분석 대상으로 합니다.

| 품목명 | 번개장터 표본 | 중고나라 표본 | 총 표본수 | 주요 대상 모델 |
|---|---:|---:|---:|---|
| **다이슨 청소기** | 569 | 674 | 1,243 | V8, V10, V11, V12, V15, SV 시리즈 등 |
| **쿠쿠 밥솥** | 761 | 966 | 1,727 | CRP, CRH, CPC, JHR, LHTR 등 |
| **메디큐브 부스터 프로** | 506 | 774 | 1,280 | AGE-R Booster Pro 등 |
| **풀리오 마사지기** | 227 | 370 | 597 | 종아리/목어깨 마사지기 시리즈 |
| **미닉스 음식물처리기** | 108 | 134 | 242 | 더 플렌더, 미닉스 감성 미니 등 |
| **베이비브레짜 분유제조기** | 35 | 119 | 154 | 포뮬러 프로 어드밴스드 등 |
| **합계** | **2,206** | **3,037** | **5,243** | **총 6개 품목군** |

---

## 3. 디렉토리 구조

```
crawler/market_analysis/
├── README.md                      # 마켓 시세 분석 전체 가이드 문서
├── data/
│   ├── raw/                       # 수집 원본 CSV 데이터셋
│   │   ├── elecmart/              # 번개장터 품목별 원본 CSV (6종)
│   │   └── joonggonara/           # 중고나라 품목별 원본 CSV (6종)
│   └── cleaned/                   # 전처리 완료 데이터 및 품질 요약
│       ├── elecmart/              # 정제된 번개장터 CSV
│       ├── joonggonara/           # 정제된 중고나라 CSV
│       ├── quality_summary.json   # 전처리 품질 요약 지표
│       ├── quality_report.md      # 데이터 정제 리포트
│       ├── rejected_rows.csv      # 제외된 행 목록 및 사유
│       └── price_anomaly_review.csv # 가격 이상치 검토 로그
├── scripts/
│   ├── marketplace_preprocess.py  # 부품/소모품 제외 및 모델명 정규화 스크립트
│   ├── statistical_analysis.py    # 통계 검정, 적정가 산출 및 analysis-data.json 생성기
│   ├── prepare_market_data.py     # SQLite 및 통합 마켓 데이터셋 구축
│   ├── build_report_artifact.py   # 아티팩트 빌더
│   └── build_notebook.py          # 분석 주피터 노트북 생성기
├── notebooks/
│   └── market_products_analysis.ipynb # 탐색적 데이터 분석(EDA) & 시각화 노트북
└── output/
    ├── analysis-data.json         # 프론트엔드 연동용 통계 JSON (사본)
    └── statistical_analysis_report.md # 최종 기술통계 및 가설검정 분석 리포트
```

---

## 4. 데이터 전처리 및 품질 관리 규칙

1. **비완제품 필터링**:
   - `부품/고장품`: `부품용`, `고장난`, `수리용`, `작동불가` 등 제외
   - `액세서리/소모품`: `필터`, `브러시`, `헤드`, `충전기`, `거치대`, `내솥`, `파우치` 등 단품 판매 제외
   - `장난감/모형/불완전상품`: `미니어처`, `본체만(배터리 없음)`, `빈 박스` 등 제외
2. **모델명 정규화 (Regex Pattern Matching)**:
   - 다이슨: `\b(?:V|DC|SV)\s*-?\s*\d+[A-Z0-9-]*` 패턴으로 정밀 매핑
   - 쿠쿠: `\b(?:CRP|CRH|CPC|CJS|JHR|LHTR|CWF|CWS|EHS|QS)\s*-?\s*[A-Z0-9-]{3,}` 매핑
   - 기타 품목: 제조사 정규 모델명 패턴 적용 (미기재 시 `제품군 공통`으로 안전하게 fallback)
3. **가격 이상치 정제**:
   - 0원 이하, 결측치, 품목별 신뢰 구간을 벗어나는 비정상 등록가 제외

---

## 5. 통계 분석 방법론

- **기술통계 (Descriptive Statistics)**:
  - 표본수 ($N$), 산술평균 ($\mu$), 10% 절사평균 (Trimmed Mean), 중앙값 (Median), 사분위수 범위 ($Q_1, Q_3, IQR$), 표준편차 ($\sigma$), 변동계수 ($CV$)
  - 95% 모평균 신뢰구간 ($95\% \text{ CI}$)
- **가설검정 (Welch's t-test)**:
  - 번개장터와 중고나라 두 플랫폼 간의 가격 차이가 유의미한지 이분산성을 가정한 Welch t-검정 수행 ($t\text{-stat}, p\text{-value}$)
  - 효과크기 산출: Cohen's $d = \frac{\bar{X}_1 - \bar{X}_2}{s_{\text{pooled}}}$
- **참고 적정가 (Reference Price)**:
  - 모델별 양 플랫폼 관측값의 통합 중앙값(Median)을 기준으로 산출

---

## 6. 실행 및 프론트엔드 동기화 방법

### 의존성 설치
```bash
pip install pandas numpy scipy
```

### 전처리 실행 (필요 시)
```bash
python crawler/market_analysis/scripts/marketplace_preprocess.py
```

### 통계 분석 및 프론트엔드 연동 JSON 생성
```bash
python crawler/market_analysis/scripts/statistical_analysis.py
```
> 실행 완료 시 `crawler/market_analysis/output/`과 함께 `front/public/analysis-data.json`이 자동으로 갱신되어 `http://localhost:3000/analysis`에 즉시 반영됩니다.
