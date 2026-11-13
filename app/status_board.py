"""오늘의 돌봄 현황 (W01) — 날짜별 요청이 지금 어느 단계인지 한눈에.

PRD FR-01(날짜·기관·서비스·우선순위 필터), FR-02(상태별 집계), FR-03(마감 임박·취소를 먼저, 우선순위 규칙 표시),
FR-04(집계 시각·자료 동기화 상태·데이터 모드). 숫자는 모두 저장소·계산 기록에서 센다(지어내지 않음).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st
import yaml

from store import Store
from store.status import (ALL_STATUSES, STATUS_CANCELLED, STATUS_CONFIRMED, STATUS_NEEDS_REVIEW,
                          STATUS_NOT_RUN, STATUS_PROPOSED, STATUS_UNASSIGNED, day_status, filter_rows,
                          summarize)

DATA = ROOT / "data" / "processed"
KST = ZoneInfo("Asia/Seoul")
SERVICE_KO = {"hospital_escort": "병원동행", "household_support": "가사지원"}
REASON_KO = {
    "TIME_WINDOW_CONFLICT": "시간이 맞는 인력 없음", "NO_CAPACITY": "인력 일정이 꽉 참",
    "NO_SKILL_MATCH": "자격 맞는 인력 없음", "ROUTE_UNKNOWN": "이동경로 확인 불가",
    "SOLVER_UNRESOLVED": "계산 중 배정 못 함", "MISSING_REQUIRED": "필수 정보 부족",
}

store = Store()

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 실제 어르신·인력 정보를 사용하지 않습니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("오늘의 돌봄 현황")


@st.cache_data(show_spinner=False)
def _dates() -> list[str]:
    from planner.data import service_dates
    return service_dates(DATA)


def _hhmm(m) -> str:
    if m is None or (isinstance(m, float) and pd.isna(m)):
        return ""
    m = int(m)
    return f"{m // 60:02d}:{m % 60:02d}"


now = dt.datetime.now(KST)
dates = _dates()
today = now.date().isoformat()
default_date = next((d for d in dates if d >= today), dates[-1])

with st.sidebar:
    st.header("보기 설정")
    date = st.selectbox("날짜", dates, index=dates.index(default_date), key="status_date")
    st.caption("기관: 가상 기관 1곳(단일 기관 범위)")
    algorithm = st.selectbox("기준 배정안", ["AI 최적화", "근거리", "신청순"],
                             help="'제안·미배정'을 어떤 방식의 계산 결과로 볼지 고릅니다. 확정은 방식과 상관없이 승인된 것입니다.")
    service = st.selectbox("서비스", ["전체", "병원동행", "가사지원"])
    priority = st.selectbox("우선순위", ["전체", "높음", "일반"])
    statuses = st.multiselect("상태", list(ALL_STATUSES), default=list(ALL_STATUSES))
    st.divider()
    ref_default = now.time().replace(second=0, microsecond=0) if date == today else dt.time(9, 0)
    ref_time = st.time_input("기준 시각", value=ref_default, step=600,
                             help="이 시각을 기준으로 '늦어도 시작할 시각'이 가까운 요청을 '임박'으로 표시합니다.")
    window = st.slider("임박 기준(분)", 30, 240, 120, step=30)

ref_min = ref_time.hour * 60 + ref_time.minute
with st.spinner("현황을 모으는 중…"):
    status = day_status(store, DATA, date, algorithm=algorithm, ref_min=ref_min, urgent_window=window)
rows = filter_rows(status["rows"],
                   service={"병원동행": "hospital_escort", "가사지원": "household_support"}.get(service),
                   priority=None if priority == "전체" else priority, statuses=statuses)
c = summarize(rows)

# FR-04 집계 시각·동기화·데이터 모드
act = store.latest_activity()
sync = " · ".join(f"{k} {v[5:16].replace('T', ' ') if v else '없음'}" for k, v in act.items())
st.caption(f"집계 시각 {now.strftime('%Y-%m-%d %H:%M')} · 데이터: **가상**(합성 어르신·인력·요청, 병원 위치는 공공데이터) · "
           f"마지막 기록: {sync}")

run = status["run"]
if run is None:
    st.info(f"{date}은(는) 지금 자료로 '{algorithm}' 계산을 한 기록이 없습니다. 그래서 확인된 요청은 '계산 전'으로 보입니다. "
            "왼쪽 메뉴 '하루 배정 비교'에서 이 날짜를 고르면 계산되고, 여기에서 제안·미배정이 보입니다.")
else:
    st.caption(f"기준 배정안: {algorithm} · 계산 실행 ID `{run['run_id']}` · 계산 시각 "
               f"{run['created_at'][:16].replace('T', ' ')}")

# 확정 일정 점검: 인력 설정·근무가 바뀐 뒤 확정과 맞지 않는 건(자동으로 바꾸지 않음)
from store.impact import confirmed_conflicts  # noqa: E402

_bad = confirmed_conflicts(store, DATA, [date])
if _bad:
    st.warning(f"확정 일정 {len(_bad)}건이 지금 인력·근무 조건과 맞지 않습니다: "
               + ", ".join(sorted({b['request_id'] or '-' for b in _bad}))
               + ". '취소·지연 대응' 또는 '하루 배정 비교'의 확정 해제로 처리해 주세요.")

# FR-02 상태별 집계 (표에 보이는 행으로 센다 → 아래 표와 숫자가 같다)
cols = st.columns(3) + st.columns(3)        # 6칸 한 줄은 이름이 잘려 3칸 두 줄로
for col, s in zip(cols, (STATUS_NEEDS_REVIEW, STATUS_NOT_RUN, STATUS_PROPOSED, STATUS_UNASSIGNED,
                         STATUS_CONFIRMED, STATUS_CANCELLED)):
    col.metric(s, f"{c[s]}건")
p1, p2, p3 = st.columns(3)
p1.metric("확정된 어르신(고유 인원)", f"{c['confirmed_people']}명", help="같은 어르신이 2건이어도 1명으로 셉니다.")
p2.metric("확정 + 제안 어르신(고유 인원)", f"{c['planned_people']}명")
p3.metric("보이는 요청", f"{c['total']}건")
st.caption("승인 대기(협약기관·인력 수락), 진행, 완료: 해당 없음 — 협약기관 수락과 인력용 앱은 후속 단계 기능입니다.")

# FR-03 먼저 볼 것
st.subheader("먼저 볼 것")
order = {"시간 지남": 0, "임박": 1}
first = [r for r in rows if r["urgent"] or r["status"] == STATUS_CANCELLED
         or (r["priority"] >= 2 and r["status"] in (STATUS_UNASSIGNED, STATUS_NEEDS_REVIEW))]
first.sort(key=lambda r: (order.get(r["urgent"], 2), -r["priority"], r["latest_start_min"]))
if not first:
    st.success("기준 시각에 급한 요청이 없습니다.")
else:
    def _why(r):
        bits = []
        if r["urgent"] == "시간 지남":
            bits.append(f"늦어도 시작할 시각({_hhmm(r['latest_start_min'])})이 지났는데 확정되지 않음")
        elif r["urgent"] == "임박":
            bits.append(f"{_hhmm(r['latest_start_min'])}까지 시작해야 함 · 아직 확정 안 됨")
        if r["status"] == STATUS_CANCELLED:
            bits.append("취소됨" + (f"({r['reason']})" if r["reason"] else ""))
        if r["priority"] >= 2 and r["status"] in (STATUS_UNASSIGNED, STATUS_NEEDS_REVIEW):
            bits.append(f"우선순위 높음({r['priority_reason']}) · {r['status']}")
        return " / ".join(bits)
    st.dataframe(pd.DataFrame([{
        "요청": r["request_id"], "대상자": r["beneficiary_id"], "서비스": SERVICE_KO[r["service_type"]],
        "상태": r["status"], "왜 먼저 보나": _why(r),
    } for r in first]), hide_index=True, width="stretch")
cfg = yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8"))
days = (cfg.get("card_intake") or {}).get("high_priority_discharge_days")
st.caption(f"우선순위 규칙: 실제 퇴원 {days}일 이내면 '높음'(2), 나머지는 '일반'(1). 배정 계산은 높은 우선순위를 먼저 채우도록 "
           "못 맡을 때 벌점을 10배로 둡니다. '임박'은 기준 시각부터 "
           f"{window}분 안에 시작해야 하는데 확정되지 않은 요청입니다.")

# 요청 목록
st.subheader(f"{date} 요청 목록")
if not rows:
    st.info("조건에 맞는 요청이 없습니다.")
else:
    st.dataframe(pd.DataFrame([{
        "요청": r["request_id"], "대상자": r["beneficiary_id"], "서비스": SERVICE_KO[r["service_type"]],
        "우선순위": "높음" if r["priority"] >= 2 else "일반",
        "희망 시작": f"{_hhmm(r['earliest_start_min'])}~{_hhmm(r['latest_start_min'])}",
        "예약": _hhmm(r["appointment_min"]), "상태": r["status"], "인력": r["worker_id"] or "",
        "도착": _hhmm(r["arrive_min"]),
        "사유": REASON_KO.get(r["reason"], r["reason"] or ""), "임박": r["urgent"], "출처": r["source"],
    } for r in sorted(rows, key=lambda r: (ALL_STATUSES.index(r["status"]), r["latest_start_min"]))]),
        hide_index=True, width="stretch")
st.caption("'계산 전'은 요청이 바뀐 뒤 아직 다시 계산하지 않았다는 뜻입니다. 예전 계산 결과를 지금 것처럼 보여주지 않습니다. "
           "가상 요청의 '당일 취소' 표시는 아직 일어나지 않은 일이라 쓰지 않고, 담당자가 처리한 취소만 보입니다.")
