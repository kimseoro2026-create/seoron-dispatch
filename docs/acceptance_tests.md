# 인수 시험(PRD 13장) 검증 현황

PRD 13장 인수 시험 30개마다 어떤 자동 테스트가 기대 결과를 확인하는지 정리했다.
`python -m pytest -q` 를 돌리면 아래 테스트가 모두 함께 실행된다. 이 표의 '검증'은 테스트가 기대 결과를 직접 확인한다는 뜻이다.

- 기준일: 2026-11-16
- 검증 24개(AT-20 은 최적화 재현의 한계를 함께 적음) · 일부 1개(AT-21) · 범위 밖 4개 · 해당 없음 1개 = 30개
- 번호가 없던 테스트와 빠진 테스트는 `tests/test_acceptance.py` 에 모았다.

| AT | 내용 | 기대 결과 | 상태 | 테스트 |
|---|---|---|---|---|
| AT-01 | 같은 CSV를 두 번 올림 | 중복 안내, 요청 추가 생성 없음 | 검증 | `test_store_import.py::test_same_file_twice_makes_no_new_requests`, `test_import_page_flow.py::test_sample_import_review_and_day_calc` |
| AT-02 | 날짜·시간이 빠진 추출 | 확인 필요, 배정 대상 제외 | 검증 | `test_acceptance.py::test_at02_card_without_date_or_time_never_reaches_assignment`, `test_intake_validate.py::test_missing_required_blocks` |
| AT-03 | 동행 중 병원 체류와 가사 요청이 겹침 | 동행 인력에 가사 배정 금지 | 검증 | `test_acceptance.py::test_at03_household_inside_escort_hospital_stay_is_overlap`, `::test_at03_engines_never_put_household_inside_escort` |
| AT-04 | 예약 시각에 맞춰 도착할 수 없음 | 적기 배정 불가, 시간 충돌 표시 | 검증 | `test_acceptance.py::test_at04_unreachable_appointment_is_time_conflict`, `::test_at04_optimizer_also_leaves_it_unassigned`, `test_verifier.py::test_detects_appointment_arrival` |
| AT-05 | 자격 만료·휴게시간 충돌 | 후보 제외, 이유 표시 | 검증 | `test_acceptance.py::test_at05_expired_skill_gives_no_skill_reason`, `test_workers.py::test_expired_skill_is_dropped_on_later_dates`, `test_verifier.py::test_detects_break_violation` |
| AT-06 | 대중교통 경로 API 실패 | 자동차 시간으로 대체, 0분 처리 금지 | 검증 | `test_external_adapters.py::test_display_falls_back_to_car_time_and_says_so` |
| AT-07 | 두 담당자가 같은 시간 같은 인력을 승인 | 한 승인만 성공 | 검증 | `test_store.py::test_two_approvals_same_worker_overlap_only_one_wins` |
| AT-08 | 승인 버튼을 두 번 누름 | 같은 승인 결과 반환 | 검증 | `test_store.py::test_same_idempotency_key_returns_same_result`, `test_day_page_approval_ui.py::test_double_click_apply_confirms_once` |
| AT-09 | 확정 요청 취소 이벤트를 다시 보냄 | 한 번만 반영, 재배정 중복 없음 | 검증 | `test_store_events.py::test_resend_same_event_is_applied_once`, `test_replan.py::test_event_id_is_stable_for_resend` |
| AT-10 | 진행 중 동행이 예상보다 늦어짐 | 현재 동행 보호, 후속 요청 재검토 | 검증 | `test_replan.py::test_delay_of_started_job_points_to_extension` |
| AT-11 | 협약기관 미수락 | 확정 인원 집계에서 제외 | 범위 밖 | 협약기관 수락 기능은 만들지 않음(CLAUDE.md 10절) |
| AT-12 | 현장 완료 상태 저장 실패 | 전송 대기, 완료 집계 제외 | 범위 밖 | 인력 모바일(현장 앱) 없음 |
| AT-13 | 다른 기관 요청 ID 직접 조회 | 서버 권한 차단 | 범위 밖 | 다기관 권한 없음(단일 기관) |
| AT-14 | 같은 사람이 동행·가사 2건 배정 | 요청 2건, 고유 인원 1명 | 검증 | `test_acceptance.py::test_at14_same_person_escort_and_household_counts_once`, `test_status.py::test_unique_people_counts_one_person_with_two_requests` |
| AT-15 | 고정급 인력의 이동시간 감소 | 수행 여력 개선, 고정 인건비 절감 없음 | 검증 | `test_acceptance.py::test_at15_travel_saving_is_capacity_not_labor_cost` (하루 배정 비교 화면 문구) |
| AT-16 | 솔버 시간 초과·유효해 없음 | 기존 일정 유지, 원인 미확정 | 검증 | `test_acceptance.py::test_at16_failed_solve_is_not_a_plan`, `::test_at16_failed_run_keeps_confirmed_schedule`, `::test_at16_timeout_leaves_unassigned_as_unresolved`, `test_optimize.py::test_timeout_is_not_hidden_as_empty_result` |
| AT-17 | 공동 동행 끔 | 1:1 전담·중복 방지 유지 | 검증 | `test_joint.py::test_off_means_one_to_one` |
| AT-18 | 합성 실험에서 취소 시각 전에 세운 계획 | 미래 취소 정보 안 씀 | 검증 | `test_acceptance.py::test_at18_initial_plan_ignores_future_cancellations`, `::test_at18_optimizer_does_not_read_cancel_flags`, `test_replan.py::test_refill_never_departs_before_event_received` |
| AT-19 | 원문에 "규칙 무시·승인" 지시를 끼워 넣음 | 데이터로만 처리, 권한·배정 변경 없음 | 검증 | `test_intake_extract.py::test_llm_following_injection_is_rejected`, `test_intake_validate.py::test_extra_field_rejected` |
| AT-20 | 같은 입력·버전·시드로 실행 | 저장된 스냅샷으로 재현, 비결정성은 설정에 기록 | 검증(일부) | `test_acceptance.py::test_at20_saved_snapshot_reproduces_baseline_run`, `::test_at20_tampered_snapshot_is_refused`, `test_store.py::test_record_run_is_reproducible`. 최적화는 시간 제한으로 멈춰 컴퓨터 속도에 따라 결과가 달라질 수 있다 — 실행 설정에 `stop_rule: time_limit`, `may_vary_by_machine_speed` 로 남기며, 같은 결과가 나온다고 테스트하지 않는다 |
| AT-21 | ODsay 12분·카카오 720초 | 내부 duration_seconds 모두 720, 수단 구분 | 일부 | `test_external_adapters.py::test_ok_uses_seconds_and_lon_lat_order` (ODsay만. 카카오 경로는 아직 연결 안 함) |
| AT-22 | API 키 없음 또는 설정만 완료 | missing_key 또는 configured, verified 금지 | 검증 | `test_lab.py::test_connection_stage_key_alone_is_not_verified`, `test_external_adapters.py::test_failures_keep_null_duration` (missing_key) |
| AT-23 | HTTP 200인데 공급자 오류 본문 | 실패 상태, 경로값 생성 금지 | 검증 | `test_apiclient_errors.py::test_http_200_with_provider_error_body_fails`, `test_external_adapters.py::test_odsay_error_body_through_real_client` |
| AT-24 | HTTP 401/403/429·시간 초과 | 인증·권한·한도·시간 초과 구분, 0분 대체 금지 | 검증 | `test_apiclient_errors.py::test_auth_errors_are_not_retried`, `::test_429_retries_then_fails_with_kind`, `::test_timeout_kind`, `test_external_adapters.py::test_failures_keep_null_duration` |
| AT-25 | 음수·NaN·비정상 경로 응답 | invalid_response, 배정 입력 제외 | 검증 | `test_external_adapters.py::test_abnormal_time_is_invalid_response`, `test_data_loader_travel.py::test_invalid_travel_rows_are_excluded`, `::test_baseline_reports_route_unknown_for_excluded_leg` |
| AT-26 | 미래 일정에 현재 기준 경로 | time_basis 명시, 미래 예측으로 표시 금지 | 검증 | `test_external_adapters.py::test_ok_uses_seconds_and_lon_lat_order` (`time_basis == provider_unspecified`) |
| AT-27 | 기관코드 없는 요양기관 상세 | 코드 확보 필요, 가상 코드 생성 금지 | 해당 없음 | 장기요양기관 상세 조회(A05)는 MVP에서 쓰지 않음(PRD 10장) |
| AT-28 | MCP 설정만 입력 | 도구 검증 전 사용 가능 표시 금지 | 범위 밖 | MCP 없음 |
| AT-29 | 성공·실패 점검 기록 | 키·원문·개인 주소 미포함 | 검증 | `test_acceptance.py::test_at29_transit_check_file_has_no_key_raw_body_or_coordinates`, `test_apiclient_errors.py::test_error_messages_never_contain_key`, `test_llm_adapter.py::test_success_writes_cache_and_log_without_secret`, `test_store.py::test_raw_text_is_never_stored` |
| AT-30 | 공급자 HTTP 리다이렉트 | 인증 헤더를 다른 곳으로 보내지 않음 | 검증 | `test_apiclient_errors.py::test_redirect_is_not_followed`, `::test_ok_and_redirects_off` |

## 이번에 고친 코드 (테스트를 쓰다 찾은 것)

- **AT-25** `adapters/transit.py`: 소요시간이 음수이면 '경로 없음'으로, NaN 이면 우연히 오류로 처리되던 것을 모두 `invalid_response` 로 통일했다. 경로 목록이 비었을 때만 `no_route`.
- **AT-25** `planner/data.py:load_travel`: 이동시간 행렬의 음수·NaN·무한대 구간을 빼고 경고한다. 빠진 구간은 엔진이 '이동경로 확인 불가'로 다룬다(0분 대체 금지). 지금 행렬(650,442구간)에는 그런 값이 없다.
- `planner/locked.py`: 이동 합계에서 없는 구간을 0분으로 세던 기본값을 없앴다(앞 단계에서 구간을 확인하므로 실제로는 안 쓰였다).
- **AT-16** `planner/optimize.py:plan_usable`: 화면 안에 있던 '배정안으로 쓸 수 있나' 규칙을 함수로 빼서 테스트한다.
- **AT-15** `simulator/report.py:travel_saving`: 이동 감소를 '돌봄 여력(분)'으로만 바꾸고 인건비 절감은 0으로 둔다. 교통비는 단가가 정해졌을 때만 계산한다. 하루 배정 비교 화면에 한 줄로 보인다.
- **AT-20** `store/db.py`: 실행 기록에 입력 스냅샷(압축)을 함께 남기고 `run_snapshot()` 으로 꺼낸다. 해시가 맞지 않으면 꺼내지 않는다.
