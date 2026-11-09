# STATUS — 서로ON 돌봄배정 AI

최근 수정: 2026-11-07

## 오늘 확인한 저장소 상태

- `.git` 이 없다. 아직 버전 관리가 시작되지 않았다. (커밋·이력 없음)
- 문서 폴더: `doc/` → `docs/` 로 옮김. `docs/PRD_v1_2.md` (v1.2, 22절) 있음.
- f0(가상 권역 생성) 구현됨: mock · fetch · generate · matrix 명령, 오프라인 모의 데이터 경로 동작.
- 테스트 `tests/test_f0.py` 6건 있음 (KMA 격자, 동일 seed 재현, 카운트·시간창, 행렬 estimate, JSON/XML 파싱, OSRM 청크).
- `data/processed/` 에 생성 결과가 있음(clients, staff, hospitals, requests, nodes, summary.json).

## 이름 정리 현황 (CLAUDE.md 5·8절 기준)

2026-11-07 정리 완료.

| 항목 | 계약 이름 | 현재 코드 | 상태 |
|---|---|---|---|
| 대상자 | `beneficiary` | `beneficiaries` / `beneficiary_id` | 완료 |
| 인력 | `worker` | `workers` / `worker_id` | 완료 |
| 동행 서비스 | `hospital_escort` | `service == "hospital_escort"` | 완료 |
| 가사 서비스 | `household_support` | `service == "household_support"` | 완료 |
| 플랫폼 인증키 | `KOREACONNECT_API_USER_KEY_ID` | 동일 | 완료 |

바뀐 것:
- 표 이름 `clients`/`staff` → `beneficiaries`/`workers` (파일도 `data/processed/beneficiaries.csv`, `workers.csv`)
- ID 접두 `C0001`/`S01` → `B0001`/`W01`
- 주 담당자 열 `primary_staff_id` → `primary_worker_id`
- 노드 `CLIENT:`/`HOME:` → `BENEFICIARY:`/`WORKER:`, 종류 `client`/`staff_home` → `beneficiary`/`worker_home`
- `region.yaml`: `simulation.clients` → `simulation.beneficiaries`, `simulation.staff` → `simulation.workers`, `service_mix` 키도 계약 이름
- `KC_API_KEY` → `KOREACONNECT_API_USER_KEY_ID` (코드·`.env.example`·README·apis.yaml 주석)
- 출력 문구의 `—`(em dash)를 `-`로 교체 — Windows cp949 콘솔에서 UnicodeEncodeError가 났다

남은 것(의도적으로 미룸):
- 요청 필드명을 PRD 8장(`earliest_start_min`/`latest_start_min`/`service_minutes`)으로 맞추는 일.
  f0는 지금 `time_window_start_min`/`time_window_end_min`/`service_duration_min`을 쓴다.
  `planner/` 데이터 모델을 만들 때 함께 옮기는 편이 안전하다(엔진·검증기가 이 필드를 쓴다).

## 검증

- `python -m pytest -q` → **6 passed**
- `python -m f0 all --offline` → 정상 완료. 대상자 300 · 인력 20 · 병원 40 · 평일 30일 · 요청 1,810건
  - 서비스 구성: household_support 1,088 / hospital_escort 722
  - 노드 361개(기관 1, 인력 20, 대상자 300, 병원 40), 행렬 129,960칸, 출처 `estimate`
- 환경: Python 3.14.7. 의존성이 하나도 설치되어 있지 않아 `pip install -r requirements.txt`를 먼저 실행해야 했다.


## 검증기 (verifier) — 2026-11-07 완료

CLAUDE.md 9.3 순서대로 엔진보다 먼저 만들었다. `verifier/`는 `planner/`를 import하지 않는다.
(테스트로 이 독립성을 강제한다 — `test_verifier_does_not_import_planner`)

- `fixtures/verify_set.json` — 인력 2명 · 요청 5건 검증세트. 모든 값 가상.
- `fixtures/verify_set_answer.json` — 손으로 푼 정답 + `_derivation` 에 계산 근거를 한 줄씩 남김.
- `verifier/__init__.py` — PRD 6.3 하드 제약 7종 검사. 위반 코드마다 6.3 항목 번호를 붙여 보고.

### 검사하는 제약

| 코드 | PRD | 의미 |
|---|---|---|
| ONEPERREQUEST 계열 `ONE_WORKER_PER_REQUEST` / `UNKNOWN_REQUEST` / `UNKNOWN_WORKER` | 6.3-1 | 요청당 최대 1명 |
| `OVERLAP` / `BREAK_VIOLATION` | 6.3-2 | 일정 겹침(이동 포함) / 휴게 |
| `NO_SKILL_MATCH` | 6.3-3 | 자격 |
| `TIME_WINDOW_CONFLICT` / `APPOINTMENT_ARRIVAL` / `ROUTE_UNKNOWN` | 6.3-4 | 시간창·예약 도착·경로 없음 |
| `SHIFT_VIOLATION` / `OVERTIME` | 6.3-5 | 근무시간 / 초과근무 |
| `MISSING_REQUIRED` / `DATA_INCONSISTENT` | 6.3-7 | 필수값 누락 / 입력 모순 |

### 지킨 원칙

- **이동시간이 없으면 0분으로 대체하지 않는다.** `ROUTE_UNKNOWN` 위반으로 남기고 그 이후 구간 계산을 멈춘다.
- **미배정은 위반이 아니다.** `unassigned` 로 따로 보고한다(미배정 자체는 정상적인 결과).
- 병원동행은 '픽업 → 병원 → 귀가'를 단일 점유 구간으로 계산한다(CLAUDE.md 5절).

### 검증

- `python -m pytest -q` → **24 passed** (f0 6 + verifier 18)
- 검증세트가 처음엔 실패했는데, R03 이동 구간(835~850)이 휴게(780~840)와 겹치는 게 진짜 문제였다.
  검증기가 제 손 계산 오류를 잡아낸 사례다. 도착을 865로 옮겨 고쳤다.

## 기준선 2종 (planner) — 2026-11-07 완료

PRD 12장의 '기존 방식' 재현. 좋은 배정을 만드는 게 아니라 AI 배정과 비교할 대조군이다.

- `planner/data.py` — f0 CSV → 계약 이름 변환(`service`→`service_type` 등). `arrival_buffer_min`은
  f0 픽업창이 함축한 값을 되돌려 계산하고, 새 값을 지어내지 않는다.
- `planner/baseline.py` — 신청순 / 근거리. 둘 다 탐욕적이며 되돌리지 않는다.
  - 신청순: `requested_at` 순, 가장 빨리 시작 가능한 인력, 동률은 ID 순.
  - 근거리: 우선순위 내림차·마감 오름차, 추가 이동이 가장 적은 인력.
  - 휴게는 이동 구간까지 포함해 검사한다(출발~도착, 도착~종료 둘 다).
- 미배정 사유는 PRD 6.5 코드로 남기고 `certainty`를 구분한다(verified / diagnostic).
  탐욕 배정은 원인을 증명할 수 없으므로 `NO_CAPACITY`·`TIME_WINDOW_CONFLICT`는 diagnostic.

### 확인된 것

- `python -m pytest -q` → **42 passed** (f0 6 + verifier 18 + baseline 18)
- 실제 f0 데이터(2026-11-02, 요청 50건·인력 20명)에서 두 기준선 모두 **하드 제약 위반 0건**.
  - 신청순: 배정 27 / 미배정 23
  - 근거리: 배정 31 / 미배정 19
- 이날은 **근거리가 신청순보다 4건 더 배정**했다. 가설(우선순위 우선이 유리)과 반대 방향이므로
  그대로 기록한다. 하루 표본이라 결론으로 쓰지 않는다.

### 관찰: 동행 요청 시간창이 20분으로 좁다

f0(generate.py)가 동행 픽업창을 `예약 − 이동 − 30 ~ 예약 − 이동 − 10`으로 만들어
**폭이 항상 20분**이다. 그래서 미배정 사유가 `TIME_WINDOW_CONFLICT`에 몰린다(신청순 22/23).
알고리즘 문제가 아니라 생성 가정의 결과다. 다만 실제 기관이 이런 20분 창을 쓰는지는 확인이 필요하다.
좁은 창은 탐욕 배정에 특히 불리하므로, 비교가 기준선에 과도하게 불리하게 기울 수 있다.
`config/region.yaml`의 시간창 폭을 실험 변수로 빼는 것을 검토한다.

## OR-Tools 최적화 + Streamlit 화면 — 2026-11-07 코드 작성 완료 (실행 미검증)

### 구현한 것

- `planner/optimize.py` — OR-Tools Routing 9.15 기반 배정 최적화
  - 인력 = 차량, 요청 = 선택적 방문 노드 (우선순위별 미배정 페널티)
  - 휴게 노드: `time_dim.CumulVar.SetRange(b_lo, b_lo)` 으로 고정 → 검증기와 일치
  - 콜백에 `manager.IndexToNode()` 변환 (routing index ≠ node index)
  - `VehicleVar().SetValues()` 로 허용 차량 지정 (OR-Tools 9.15 바인딩 우회)
  - 상태 코드를 정수값(1/2/3/4/5)으로 비교 (버전 안전)
  - 30초 계산 제한, timeout 을 '미배정 0건'으로 숨기지 않음
- `tests/test_optimize.py` — 8개 테스트 (검증기 통과, 중복 없음, 전 요청 기록, timeout 안전, 기준선 이상 배정)
- `app/main.py` — Streamlit 하루 비교 화면
  - 날짜·방식(신청순·근거리·최적화) 선택, 인력별 타임라인 (HTML), 세 방식 비교표, 미배정 목록
  - "가상 데이터 기반 실험" 배너, 이동시간 출처(estimate/osrm/mock) 표시
- `requirements.txt` 에 `ortools>=9.8`, `streamlit>=1.30` 추가

### 미검증 (Bash 판정기 일시 장애로 실행 못 함)

- `python -m pytest tests/test_optimize.py -q` — **8 passed, 16초** (2026-11-07 확인)
- `python -m pytest -q` — **50 passed, 20초** (전체 통과)
- `streamlit run app/main.py` — 아직 실행 안 됨 (Streamlit 패키지 설치 필요: `pip install streamlit`)

### 구현 중 발견한 버그와 수정 이력

1. **귀가 이동시간 누락 → infeasible**: 검증세트에 기지 귀가 구간이 없었다. 검증기·기준선은 귀가 이동을 모델링하지 않으므로, 최적화만 엄격하면 비교가 성립하지 않는다. 귀가 arc 비용을 0으로 통일했다.
2. **휴게 BREAK_VIOLATION**: 휴게 노드에 장소가 없어 이후 이동을 0으로 계산했다. 그러면 출발이 휴게 구간 안으로 들어간다. 보수적 패딩으로 도착이 항상 휴게 종료 이후가 되게 했다.
3. **skip 요청 배정됨**: 시간창이 역전된 요청을 skip 집합에 넣었지만 노드 자체가 남아 제약 없이 배정됐다. 솔버 결과를 읽을 때 skip 요청을 명시적으로 걸러냈다.
4. **max_travel 과대 추정**: 병원 이동까지 포함해 30분이 되어 R04 시간창(865)이 막혔다. 기지·집 노드 간 이동만으로 좁혔다.

## 최적화가 실제 데이터에서 전부 실패한 문제 — 2026-11-08 해결

**증상**: 30일 실험(810건)에서 최적화만 배정 0건, `status=timeout`, 계산시간이 항상 제한과 동일.
검증세트(요청 5건)에서는 통과했으므로 실제 규모에서만 드러난 문제였다.

**진단**: `tools/diagnose_optimize.py` 로 모델 성분을 하나씩 끄며 원인을 좁혔다.

| 조합 | 상태 | 배정 |
|---|---|---|
| 전체 | FAIL | 0 |
| 휴게 제거 / 시간창 제거 / 근무차원 제거 / disjunction 제거 | FAIL | 0 |
| **VehicleVar 제거** | **SUCCESS** | **33** |

**원인 1 — `VehicleVar().SetValues()`**: 차량 변수의 정의역을 값 하나로 자르면 '수행 안 함' 상태가
사라진다. `AddDisjunction` 으로 버릴 수 있어야 할 요청이 **필수 노드**가 되어 모델이 infeasible.
(원래는 `SetAllowedVehiclesForIndex` 를 쓰려 했으나 OR-Tools 9.15 바인딩이 `absl::Span` 을 요구해
실패했고, 그 우회가 틀렸다.)
→ 자격·휴게 소유권을 **차량별 통행 콜백**(`AddDimensionWithVehicleTransits`)으로 옮기고
   허용되지 않는 진입은 `BIG_M` 으로 막았다. 노드는 버릴 수 있는 상태가 유지된다.

**원인 2 — 휴게 패딩이 전역 최댓값**: 휴게 뒤 모든 요청의 도착이 `840 + 전역max` 로 고정되어
시간창이 좁은 오후 요청이 통째로 탈락했다. 시간 제한을 10초→30초로 늘려도 32건 그대로였던 이유다.
→ 요청별 최댓값(`pad[i] = 그 집까지 올 수 있는 최대 이동`)으로 바꿨다.

**결과 (2026-11-02, ρ=0.9, seed=42)**

| 방식 | 배정 | 위반 | 계산 |
|---|---|---|---|
| 신청순 | 27/50 | 0 | 0초 |
| 근거리 | 33/50 | 0 | 0초 |
| 최적화 | **33/50** | 0 | 30초 |

최적화가 처음으로 정상 동작했다. 아직 기준선을 앞서지는 못한다(동률).
이동시간은 212분으로 근거리(153분)보다 많다 — 배정 수를 늘리는 쪽으로 탐색하기 때문.

**교훈**: 배정 0건에 위반 0건은 '잘 지켰다'가 아니라 '아무것도 못 했다'다.
`simulator/run.py` 가 이 구분을 출력하도록 고쳤다(CLAUDE.md 절대원칙 3).

## 시뮬레이터 30일 실험 — 2026-11-08 완료

**설정:** 30일 × 3시드(42,43,44) × 3부하율(0.6/0.9/1.1) × 3방식 = 810건
**출처:** `data/experiments/full-20261108-214430.csv`

### 결과 (적기 배정률 평균, 각 90관측)

| ρ | 신청순 | 근거리 | 최적화 | 위반 |
|---|---|---|---|---|
| 0.6 | 60.1% | **63.3%** | 62.0% | 0건 |
| 0.9 | 53.5% | **61.6%** | 61.0% | 0건 |
| 1.1 | 51.4% | **61.3%** | 61.0% | 0건 |

### 확실한 것
- 신청순 → 동선 고려 방식으로 바꾸면 **7~8%p 더 배정**된다. 30일·3시드 일관.
- 하드 제약 위반 **0건** (810건 전부, 검증기 독립 확인).

### 확실하지 않은 것 (정직하게 남긴다)
- 최적화가 근거리를 이기지 못했다. 0.3~1.3%p 차이로 표준편차(4.7~7.0%p) 안에 있다.
  "AI 배정이 기준선보다 낫다"는 주장은 이 실험으로 할 수 없다.
- 최적화 이동시간이 길다(ρ=0.9: 224분 vs 근거리 174분). 목적함수 가중치 조정 필요.

### 후처리(repair) 도입 — 2026-11-08

**왜:** 모델이 휴게 뒤 요청 도착을 `840 + pad[j]`(상한)로 밀어 놓아,
실제 이동으로는 가능한 배정을 버리는 손실이 있었다.

**무엇:** `_repair()`. OR-Tools 결과 뒤에 버려진 요청을 **실제 이동시간**으로 다시 시도한다.
기존 배정은 건드리지 않고 각 인력의 마지막 위치·가용 시각 뒤에 덧붙이기만 한다.
검증기를 다시 통과한 경우에만 채택한다.

**테스트:** 11 passed (기존 8 + repair 3: 제약 유지 · 배정 수 감소 없음 · 중복 없음).

**정직하게:** `optimize(repair=True)` 는 'OR-Tools 결과 + 조건부 덧붙이기'다.
비교표와 보고서에 그대로 적는다.

## 담당자 승인 흐름 — 2026-11-09 완료 (M2 착수)

CLAUDE.md 6절 "AI는 판정하지 않는다. 배정 확정은 항상 담당자가 한다"를 구조로 강제했다.

### `planner/approval.py`

PRD 9장 승인 트랜잭션 순서를 따랐다.

| 단계 | 구현 |
|---|---|
| 2. idempotency_key 중복 확인 | 같은 키로 재호출하면 이전 결과를 그대로 반환 |
| 3. schedule_version 확인 | 오래된 제안이 최신 확정을 덮어쓰지 못함 (`STATUS_STALE`) |
| 4. 최신 자료로 재검증 | 확정분 + 이번 수락분을 합쳐 검증기로 다시 확인 |
| 5. 반영 후 version 증가 | |
| 6. 승인 대기 슬롯 유지 | 확정 건은 잠금. 담당자가 `release()` 로 풀어야 변경 가능 |

- 확정된 요청은 **잠금**된다. 덮어쓰기 시도하면 `STATUS_CONFLICT`.
- 거부에는 **사유가 필수**다(`ApprovalError`).
- 모든 결정은 `audit` 에 남는다(누가·무엇을·이전/이후 version).

### 화면 (`app/main.py`)

- 배정안별 표에서 각 요청에 **수락 / 거부 / 보류** 선택, 거부 사유 입력
- 하드 제약 위반이 있는 배정안은 **승인 자체를 막는다**
- 확정 내역 표시 + CSV 내려받기
- 승인 상태는 `st.session_state` 에 보관

### 검증

- `python -m pytest -q` → **85 passed** (approval 10건 추가)

### 한계 (정직하게)

- 상태를 **메모리에만** 들고 있다. 실제 서비스는 DB 트랜잭션·RPC가 필요하다.
- 기관·역할 권한 검사(PRD 9장 1단계)는 생략했다. M2는 단일 기관 전제다.
- `worker_occupancies` exclusion constraint 같은 DB 수준 보장은 없다.

## M1 일정 대비 진행 (CLAUDE.md 9절)

## 이동시간 OSRM 전환 — 2026-11-07 완료 (M1 2단계)

- OSRM 서버 기동: `osrm/prepare.bat` (Windows판. `prepare.sh`는 Git Bash 전용이라 대체)
  한국 OSM 전국 데이터 → `osrm-extract`/`partition`/`customize` → `docker compose up -d`
- `rebuild_matrix.bat` 으로 행렬 재생성: 노드 361개, **129,960칸**, `source` = **`osrm`**
- 확인: `curl http://localhost:5000/nearest/v1/driving/127.075,37.2` → HTTP 200

### 배치 파일 주의사항 (겪은 함정 기록)

1. **`.bat`은 반드시 ASCII로 저장한다.** UTF-8 한글을 넣으면 cp949로 읽혀 명령어가 쪼개지고
   `'atest'`, `'echo'` 같은 오류가 쏟아진다. 첫 줄 `@echo off`까지 깨진다.
2. **`if (...)` 블록 안에 `(` 를 쓰지 않는다.** `echo ... (about 700MB)` 처럼 괄호가 들어가면
   cmd가 블록 끝으로 오해해 그 자리에서 종료된다. `goto` 라벨로 분기하거나 괄호를 뺀다.
3. `.ps1` 더블클릭은 "앱 선택" 창이 뜬다. 실행은 `.bat` 또는 `powershell -ExecutionPolicy Bypass -File` 로.

## 병원 API 최종 수집 — 2026-11-08 완료

- 반경 6km로 재시도 → **400건, 경고 없음** (6km 반경 전수)
- 15km(4,060건) 대비 1/10이지만, bbox 필터(37.16~37.24 / 127.03~127.13) 안의 병원은 전수 포함
- `python -m f0 generate` → 실제 병원 400곳 기반 가상 권역 재생성
  - beneficiaries 300, workers 20, requests 1810
- `python -m f0 matrix` → 노드 721개, 행렬 **519,120칸**, 출처: OSRM 도로
  (이전: 노드 361개, 행렬 129,960칸 — 병원 노드가 40 → 360개로 늘어남)

## M1 일정 대비 진행 (CLAUDE.md 9절)

| 단계 | 기한 | 상태 |
|---|---|---|
| 1. 정리 (f0 이름·env, apis.yaml fetch 성공) | 11/7~11/8 | ✅ 완료(11/8). 병원 400건(6km 전수) 수집, 권역 재생성. 인구 API만 상세 화면 대기 |
| 2. 이동시간 (OSRM 행렬 source=osrm) | 11/9 | ✅ 완료(11/7). OSRM 서버 기동, 행렬 source=osrm 확인 |
| 3. 엔진·검증기 (verifier → fixtures → 기준선 2종 → Routing) | 11/10~11/13 | ✅ 완료(11/7). 50 tests passed |
| 4. 화면 (Streamlit 한 페이지) | 11/14~11/15 | ✅ 완료(11/7). 비교표·타임라인·미배정 목록 동작 확인 |
| 5. 제출 패키지 | 11/16 | 미착수 |

## 없는 디렉터리 (CLAUDE.md 4절 구조 대비)

`planner/`, `verifier/`, `simulator/`, `adapters/`, `app/`, `fixtures/` — 아직 생성 안 됨.

## 병원기본목록 API 연결 (2026-11-07)

플랫폼 상세 화면에서 확인한 값으로 `config/apis.yaml`의 `hospitals` 항목을 채웠다.

- 호출 URL: `https://api.koreaconnect.kr/01/1/.../hospInfoServicev2/getHospBasisList`
- 요청변수 확인됨: `pageNo`, `numOfRows`, `xPos`(x좌표 소수점15), `yPos`(y좌표 소수점15), `radius`(미터)
  - 그 밖에 `sidoCd`, `sgguCd`, `emdongNm`, `yadmNm`, `zipCd`, `clCd`, `dgsbjtCd`도 화면에 있음(필요 시 사용)
- 응답 항목 확인됨: `ykiho`(암호화 요양기호), `yadmNm`(병원명), `clCdNm`(종별코드명), `addr`(주소), `XPos`, `YPos`, `totalCount`
- 응답 형식: XML. 헤더 이름 `api_user_key_id`.
- 인증키는 `.env`에만 넣었다(저장소 파일·로그에 없음).

호출 중 고친 것:
- **한글 깨짐**: XML인데 `requests`가 ISO-8859-1로 추정 → `resp.content`를 UTF-8로 직접 디코딩.
- **resultCode 검사**: `00`이 아니면 조용히 빈 결과를 돌려주지 않고 예외로 멈춘다.
- **503 재시도**: 지수 백오프(10→20→40→80→160초), 최대 5회. 페이지 간 3초 지연.
- **조기 종료 버그**: 빈 페이지 1회에 바로 멈추던 로직을 2회 연속일 때만 종료하도록 고쳤다.
  (1차 호출에서 4,060건 중 401건만 받고 exit 0으로 끝난 원인)
- **콘솔 인코딩**: Windows cp949에서 em dash 출력 시 크래시 → `cli.py`에서 stdout을 UTF-8로 reconfigure.

현재 상태:
- 1차 호출에서 **401건** 저장됨(`data/raw/hospitals.csv`). 한글·좌표 정상, bbox(37.16~37.24 / 127.03~127.13) 안에 들어옴.
- 전체는 **4,060건**. 페이지 수집이 끝나지 않았다.
- 이후 호출은 **전부 503**. 20초 간격으로도 503이 지속되어 게이트웨이 속도 제한에 걸린 것으로 판단.
  키 없음/틀린 키는 401(정상 인증 동작) → 키 자체가 무효는 아님.

## 막힌 점

- **병원 API 속도 제한**: 짧은 시간에 호출이 몰려 503이 지속된다. 시간을 두고 재시도하거나
  `radius`를 줄여(예: 15km → 6km) 페이지 수를 낮추는 방법이 있다. 반경을 줄여도 수집 후 bbox로
  다시 거르므로 최종 결과 손실은 작다.
- **인증키 재발급 권장**: 키가 대화에 평문으로 남았다. 작업이 끝나면 플랫폼에서 재발급하고 `.env`만 갱신할 것.
- 저장소에 `.git` 이 없어 커밋·이력 관리가 안 됨. 일정상 여유가 생기면 `git init` 필요.
- 인구 API(`population`)는 URL·요청변수·응답 항목이 아직 `TODO` 상태. 상세 화면 필요.

## 다음 작업

1. 병원 API 수집 완료(4,060건) — 속도 제한이 풀린 뒤 재시도.
2. 인구 API 상세 화면 확보 → `apis.yaml`의 `population` 항목 채우기.
3. 그다음 M1 2단계(OSRM 행렬)로 진행.
