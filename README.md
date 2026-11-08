# 서로ON 돌봄배정 AI — F0 가상 구 데이터

동탄 권역을 '가상 구'로 만들어 배정 엔진(F3)과 시뮬레이터(F7)의 입력을 준비하는 단계입니다.
실제 병원 위치·고령인구 분포·도로 이동시간 위에 **가상의** 어르신·인력·요청을 생성합니다.
실제 개인정보는 사용하지 않습니다.

## 폴더 구조

```
config/region.yaml    권역 범위, 시뮬레이션 가정값(인터뷰 후 교체)
config/apis.yaml      디지털융합플랫폼 API 호출 설정(URL을 상세 화면에서 채움)
f0/                   수집·생성·행렬 코드
osrm/                 OSRM 경로 서버 준비 스크립트
sql/schema.sql        Supabase 스키마
tests/                테스트
data/raw/             원천 데이터(병원·인구·경계)
data/processed/       생성 결과(clients, staff, hospitals, requests, nodes, travel_matrix, summary.json)
```

## 1. 바로 돌려보기 (모의 데이터, 인터넷·API 불필요)

```bash
pip install -r requirements.txt
python -m f0 all --offline
python -m pytest -q
```

어르신 300명, 인력 20명, 병원 40곳, 평일 30일 치 요청 약 1,800건이 생성됩니다.
행정동·병원 이름은 '가상동1', '가상종합병원1'처럼 모두 가짜입니다.

## 2. 실제 데이터로 바꾸기

1. `.env.example`을 `.env`로 복사하고 `KOREACONNECT_API_USER_KEY_ID`에 플랫폼 인증키를 넣습니다.
2. `config/apis.yaml`의 `TODO`를 플랫폼 상세 화면 내용으로 채웁니다.
    - `hospitals.url`: 병원기본목록의 호출 URL. 요청변수 이름(xPos, yPos, radius 등)이 다르면 맞춰 고칩니다.
    - `population.url`, `params`, `field_map`: 인구 API의 호출 URL, 요청변수(행정기관코드·기준월), 행정동명 항목과 65세 이상 연령 구간 항목명.
    - `weather.url`, `transit.url`: 단기예보·대중교통 길찾기의 호출 URL.
3. 최신 행정동 경계를 GeoJSON(위경도, WGS84)으로 `data/raw/boundaries.geojson`에 넣고,
   행정동명 속성 이름을 `region.yaml`의 `dong_name_field`에 적습니다.
   (플랫폼에는 2017·2018년 경계만 있어 동탄 신설 행정동이 빠질 수 있습니다. 통계청 SGIS 등에서 최신본을 받습니다.)
4. 실행합니다.

```bash
python -m f0 fetch --population-param 기준월=202609     # 요청변수 이름은 상세 화면 기준
python -m f0 generate
python -m f0 matrix
```

인구 표의 행정동명이 경계 파일의 행정동명과 같아야 합니다. 하나도 맞지 않으면 오류로 알려줍니다.

## 3. OSRM 연결 (차량 이동시간)

`osrm/README.md`를 따라 서버를 켜고 `.env`에 `OSRM_URL=http://localhost:5000`을 넣은 뒤
`python -m f0 matrix`를 다시 실행합니다. 서버가 없으면 직선거리 기반 대략값으로 계산하고,
`travel_matrix.csv`의 `source` 열에 `estimate`로 표시합니다.

## 가정값 (config/region.yaml)

| 항목 | 초기값 | 교체 근거 |
|---|---|---|
| 하루 요청 수 | 평균 60건 | 협력기관 일 배정 건수 |
| 서비스 구성 | 동행 40%, 가사 60% | 기관 실적 |
| 병원 체류 | 120~240분 | 현장 인력 인터뷰 |
| 가사 1회 | 120분 | 계약 기준 |
| 당일 취소율 | 10% | 배정 담당자 인터뷰 |
| 인력 | 동행 8명, 가사 12명, 09~18시 | 기관 인력 현황 |

## 생성 규칙 요약

- 어르신 위치: 행정동별 65세 이상 인구 비율대로 경계 안에 무작위 배치
- 주 담당자: 가장 가까운 가사 인력, 1인당 상한을 두어 고르게 배정(담당 연속성용)
- 동행 병원: 종별 가중치 × 거리 감소(5km)로 선택
- 예약 시각: 오전 60%, 근무 시작 후 도착 가능한 시각 이후로 보정
- 픽업 시간창: 예약 시각 기준 30~10분 전 도착하도록 설정
- `requested_at`: 서비스일 1~7일 전 신청 시각(기준선 '신청순 배정'의 정렬 기준)
