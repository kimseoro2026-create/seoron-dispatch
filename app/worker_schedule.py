"""인력 일정(읽기 전용) — PRD W08 최소판. 인력 한 명의 확정 일정과 필요한 정보만 보여준다.

FR-33: 본인 일정·필요 지원조건·시간·필요 위치만. 수락/거절·이동·시작·완료 버튼(FR-34·35)과 로그인·권한은 후속 단계라 없다.
확정되지 않은 제안은 보여주지 않는다(확정만이 인력의 일정이다).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from store import Store

DATA = ROOT / "data" / "processed"
MOBILITY_KO = {"independent": "자립 보행", "walker": "보행기 사용", "wheelchair": "휠체어 사용"}
SERVICE_KO = {"hospital_escort": "병원동행", "household_support": "가사지원"}

store = Store()

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 인력·어르신은 모두 가상입니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("인력 일정 (읽기 전용)")
st.caption("인력 한 명의 확정된 하루 일정을 봅니다. 확정되지 않은 제안은 보이지 않습니다. "
           "인력용 로그인·수락/완료 버튼은 후속 단계 기능이라 아직 없습니다(지금은 담당자 화면에서 미리 보기용).")


def _hhmm(m) -> str:
    return "" if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"


@st.cache_data(show_spinner=False)
def _static():
    from planner.data import service_dates
    hos = pd.read_csv(DATA / "hospitals.csv", dtype={"hospital_id": str}) if (DATA / "hospitals.csv").exists() \
        else pd.read_csv(ROOT / "data" / "raw" / "hospitals.csv", dtype={"hospital_id": str})
    ben = pd.read_csv(DATA / "beneficiaries.csv", dtype={"beneficiary_id": str})
    wk = pd.read_csv(DATA / "workers.csv", dtype={"worker_id": str})
    return (service_dates(DATA), dict(zip(hos["hospital_id"], hos["name"])),
            dict(zip(ben["beneficiary_id"], ben["mobility"])), list(wk["worker_id"]))


dates, hosp_name, mobility, worker_ids = _static()
dates_with = store.confirmed_dates()
c1, c2 = st.columns(2)
date = c1.selectbox("날짜", dates, index=dates.index(dates_with[0]) if dates_with else 0)
busy = sorted({c["worker_id"] for c in store.confirmed(date)})
wid = c2.selectbox("인력", worker_ids, index=worker_ids.index(busy[0]) if busy else 0,
                   format_func=lambda w: f"{w} (확정 {sum(1 for c in store.confirmed(date) if c['worker_id'] == w)}건)")

jobs = sorted((c for c in store.confirmed(date) if c["worker_id"] == wid), key=lambda c: c["arrive_min"])
if not jobs:
    st.info(f"{date}에 {wid}의 확정 일정이 없습니다. '하루 배정 비교'에서 승인하면 여기에 보입니다.")
    st.stop()

from planner.baseline import _occupancy  # noqa: E402
from planner.data import load_day  # noqa: E402

inst = load_day(DATA, date, **store.day_inputs(date))
by_id = {r["request_id"]: r for r in inst.requests}
worker = next((w for w in inst.workers if w["worker_id"] == wid), None)
if worker is not None:
    st.markdown(f"**근무** {_hhmm(worker['shift_start_min'])}~{_hhmm(worker['shift_end_min'])} · "
                f"**휴게** {_hhmm(worker['break_start_min'])}부터 {worker['break_minutes']}분")
rows, missing = [], []
for j in jobs:
    r = by_id.get(j["request_id"])
    if r is None:
        missing.append(j["request_id"])
        continue
    end, _ = _occupancy(r, inst.travel, int(j["arrive_min"]))
    escort = r["service_type"] == "hospital_escort"
    place = f"어르신 댁({r['beneficiary_id']})"
    if escort:
        place += f" → {hosp_name.get(r['hospital_node'].removeprefix('HOSP:'), '병원')} → 댁"
    rows.append({
        "도착": _hhmm(j["arrive_min"]), "끝": _hhmm(end) if end is not None else "경로 확인 필요",
        "서비스": SERVICE_KO.get(r["service_type"], r["service_type"]), "대상자": r["beneficiary_id"],
        "필요한 위치": place, "병원 예약": _hhmm(r.get("appointment_min")) if escort else "",
        "지원 조건": MOBILITY_KO.get(mobility.get(r["beneficiary_id"]), "확인 필요"),
    })
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
if missing:
    st.warning("확정됐지만 지금 요청 목록에 없는 일정이 있습니다(취소·변경 확인 필요): " + ", ".join(missing))
st.caption("이 화면은 이름·연락처를 보여주지 않습니다(가상 ID만). 지원 조건은 가상 자료의 이동조건이며 의료적 판단이 아닙니다.")
