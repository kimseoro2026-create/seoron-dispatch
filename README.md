# 서로ON 돌봄배정 AI

퇴원 어르신의 **병원동행·가사지원 요청**과 돌봄 인력의 **근무시간·휴게·자격·동선**을 함께 계산해,
같은 인력과 예산으로 **제때 돌봄받는 어르신을 늘리는** 담당자 승인형 배정 시스템입니다.

- 심사용 화면: https://seoron-dispatch-blgk8n6odqv5cdgflu3asd.streamlit.app
- M1 요약: [docs/submission/summary_1page.md](docs/submission/summary_1page.md) · 수치 출처: [docs/submission/numbers.md](docs/submission/numbers.md)
- 작업 기록: [docs/STATUS.md](docs/STATUS.md) · 요구사항: [docs/PRD_v1_2.md](docs/PRD_v1_2.md)

> **모든 어르신·인력·요청은 가상(합성) 데이터입니다.** 실제 개인정보를 사용하지 않습니다.
> 병원 위치는 심평원 병원기본목록 API(400곳), 이동시간은 자체 OSRM 도로 경로입니다. 고령인구는 아직 모의값입니다.

## 무엇이 들어 있나

| 폴더 | 내용 |
|---|---|
| `f0/` | 가상 권역 생성: 병원·인구·경계 → 어르신·인력·요청, 이동시간 행렬 |
| `planner/` | 데이터 모델, 기준선(신청순·근거리), OR-Tools Routing 최적화, 취소·지연 재배정, 담당자 승인 |
| `verifier/` | 엔진과 독립된 하드 제약 검증기(`planner`를 import 하지 않음) |
| `simulator/` | 30일 × 다중 시드 × 부하율 실험, 지표 계산 |
| `app/` | Streamlit 화면 |
| `tools/` | 실험·진단·제출 수치표 스크립트 |
| `scripts/` | Windows 실행용 배치 파일 |
| `config/` | `region.yaml`(권역·실험 가정), `apis.yaml`(API 호출 설정, 키 제외) |
| `fixtures/` | 2명·5건 검증세트와 손으로 푼 정답 |
| `osrm/` | OSRM 경로 서버 준비 스크립트 |
| `data/` | 원천(`raw/`)·생성 결과(`processed/`)·실험 결과(`experiments/`) |
| `tests/` | pytest |

## 바로 실행하기

```bash
pip install -r requirements.txt
python -m pytest -q                 # 테스트
streamlit run app/main.py           # 화면 (Windows: scripts\run_app.bat)
```

저장소에 생성된 권역과 이동시간 행렬 압축본(`travel_matrix.csv.gz`)이 들어 있어 바로 화면이 뜹니다.
Streamlit Community Cloud 배포 시 Main file 은 `app/main.py`, Python 은 3.12 또는 3.13 을 고릅니다.

## 실험 다시 돌리기

```bash
python -m simulator.run --tag full      # 3방식 × 부하율 0.6/0.9/1.1 × 30일 × 3시드 (약 45분)
python -m tools.run_replay30            # 취소·지연 재생 30일 실험
python -m tools.submission_numbers      # docs/submission/numbers.md 다시 계산
```

결과는 `data/experiments/`에 시각이 붙은 CSV로 저장되고, 화면은 가장 최근 파일을 읽습니다.
모든 결과는 `verifier/`로 다시 검증하며 위반이 있는 배정안은 사용하지 않습니다.

## 가상 권역 다시 만들기

> ⚠️ `python -m f0 all --offline` 과 `python -m f0 mock` 은 `data/raw/`를 **모의 데이터로 덮어씁니다.**
> 실제 병원 데이터로 만든 권역에서는 `generate`·`matrix`만 실행하세요.

### 모의 데이터로 (인터넷·API 불필요)

```bash
python -m f0 all --offline
```

행정동·병원 이름이 '가상동1', '가상종합병원1'처럼 모두 가짜인 권역이 생성됩니다.

### 실제 데이터로

1. `.env.example`을 `.env`로 복사하고 `KOREACONNECT_API_USER_KEY_ID`에 플랫폼 인증키를 넣습니다. (`.env`는 git 에서 제외됩니다)
2. `config/apis.yaml`의 `TODO`를 플랫폼 상세 화면 내용으로 채웁니다. (병원은 완료, 인구는 상세 화면 대기)
3. 최신 행정동 경계를 GeoJSON(WGS84)으로 `data/raw/boundaries.geojson`에 넣고, 행정동명 속성 이름을 `region.yaml`의 `dong_name_field`에 적습니다.
4. 실행합니다.

```bash
python -m f0 fetch      # 원천 수집 (병원만: --skip-population)
python -m f0 generate   # 어르신·인력·요청 생성
python -m f0 matrix     # 이동시간 행렬 (OSRM 이 없으면 직선거리 대략값, source=estimate 로 표시)
```

OSRM 서버는 `osrm/README.md`를 따라 켜고 `.env`에 `OSRM_URL=http://localhost:5000`을 넣습니다.

## 가정값 (config/region.yaml)

실험용 초기 가정이며, 기관 인터뷰 후 교체합니다.

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
- 픽업 시간창: 예약 시각 기준 30~10분 전 도착하도록 설정(폭 20분 — 미배정의 주된 원인, 한계로 기록)
- `requested_at`: 서비스일 1~7일 전 신청 시각(기준선 '신청순 배정'의 정렬 기준)
