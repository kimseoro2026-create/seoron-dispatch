"""취소·지연 대응 (W07) — 확정된 하루 일정에 취소·지연을 등록하면 바뀔 일정을 먼저 보여주고, 담당자가 적용한다.

PRD FR-29(실제 발생 시각·접수 시각 기록), FR-30(진행 중 보호, 비워진 시간만 다시 계산),
FR-31(영향받는 어르신·인력과 필요한 확인), FR-32(대체 요청 없음/자료 부족/경로 불명/시간 충돌 구분, 자동 파견·메시지 없음).
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

from planner.replan import (KIND_CANCEL, KIND_DELAY, KIND_EXTEND, KIND_KO, REFILL_REASON_KO, event_id,
                            plan_event)
from store import Store

DATA = ROOT / "data" / "processed"
KST = ZoneInfo("Asia/Seoul")
SERVICE_KO = {"hospital_escort": "병원동행", "household_support": "가사지원"}
REASONS = ["대상자 요청", "건강 상태 변화", "병원 사정(대기·검사 길어짐)", "교통·이동", "인력 사정", "기타"]

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
st.title("취소·지연 대응")
st.caption("확정된 일정에 취소나 지연이 생기면 여기에 등록하세요. 바뀔 일정을 먼저 보여주고, **담당자가 '적용'을 눌러야** 바뀝니다. "
           "이미 시작한 돌봄은 지키고, 비워진 시간만 다시 맞춥니다. 어르신·인력에게 알림을 보내지 않습니다(연락은 담당자가 합니다).")


def _hhmm(m) -> str:
    return "" if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def _mins(t: dt.time) -> int:
    return t.hour * 60 + t.minute


@st.cache_data(show_spinner=False)
def _dates() -> list[str]:
    from planner.data import service_dates
    return service_dates(DATA)


def _instance(date: str):
    from planner.data import load_day
    return load_day(DATA, date, **store.day_inputs(date))


dates = _dates()
today = dt.datetime.now(KST).date().isoformat()
with st.sidebar:
    st.header("보기 설정")
    date = st.selectbox("날짜", dates, index=dates.index(next((d for d in dates if d >= today), dates[-1])),
                        key="event_date")

inst = _instance(date)
by_id = {r["request_id"]: r for r in inst.requests}
confirmed = store.confirmed(date)
conf_by = {c["request_id"]: c for c in confirmed}
version = store.schedule_version(date)

st.subheader(f"{date} 확정 일정")
if not confirmed:
    st.info("이 날짜에 확정된 일정이 없습니다. '하루 배정 비교'에서 승인한 뒤 사용하세요. "
            "확정되지 않은 요청의 취소는 지금도 등록할 수 있습니다.")
else:
    st.caption(f"확정 {len(confirmed)}건 · 인력 {len({c['worker_id'] for c in confirmed})}명 · 일정 버전 {version}")

# ── 이벤트 등록 ──────────────────────────────────────────────────────────
st.subheader("① 무슨 일이 생겼나요")
kind_label = st.radio("종류", [KIND_KO[KIND_CANCEL], KIND_KO[KIND_DELAY], KIND_KO[KIND_EXTEND]], horizontal=True,
                      help="시작 지연: 아직 시작 전인데 늦어짐 · 진행 중 연장: 이미 시작한 동행·서비스가 길어짐(병원 대기 등)")
kind = {v: k for k, v in KIND_KO.items()}[kind_label]


def _label(rid: str) -> str:
    r = by_id[rid]
    c = conf_by.get(rid)
    tail = f"확정 {c['worker_id']} {_hhmm(c['arrive_min'])}" if c else "확정 안 됨"
    return f"{rid} · {r['beneficiary_id']} · {SERVICE_KO[r['service_type']]} · {tail}"


options = sorted(conf_by) if kind == KIND_EXTEND else sorted(by_id, key=lambda x: (x not in conf_by, x))
if not options:
    st.info("고를 수 있는 요청이 없습니다.")
    st.stop()
rid = st.selectbox("요청", options, format_func=_label)
c1, c2, c3 = st.columns(3)
occurred = c1.time_input("실제 발생 시각", value=dt.time(9, 30), step=300)
received = c2.time_input("접수 시각", value=dt.time(9, 30), step=300,
                         help="담당자가 알게 된 시각. 이 시각 이후로만 일정을 바꿉니다.")
minutes = c3.number_input("늦어지는 시간(분)", min_value=0, max_value=480, value=30, step=5,
                          disabled=kind == KIND_CANCEL)
reason = st.selectbox("사유", REASONS)
refill = st.checkbox("빈자리 채우기 제안 포함", value=True,
                     help="취소·지연으로 빈 시간에 넣을 수 있는 요청을 한 건 찾아 제안합니다(적용 전까지 확정 아님).")

ev = {"kind": kind, "request_id": rid, "occurred_min": _mins(occurred), "received_min": _mins(received),
      "minutes": 0 if kind == KIND_CANCEL else int(minutes), "reason_code": reason}
eid = event_id(date, ev)
prop_key = f"proposal_{date}"

if st.button("바뀔 일정 계산", type="primary"):
    p = plan_event(inst.as_instance(), confirmed, ev, refill=refill)
    st.session_state[prop_key] = {"eid": eid, "ev": ev, "version": version, "proposal": p}

# ── 제안 확인 ────────────────────────────────────────────────────────────
saved = st.session_state.get(prop_key)
if saved:
    p = saved["proposal"]
    st.subheader("② 바뀔 일정 확인")
    if saved["eid"] != eid:
        st.warning("위 내용이 계산한 뒤에 바뀌었습니다. '바뀔 일정 계산'을 다시 눌러 주세요.")
    (st.success if p.ok else st.error)(p.message)
    for n in p.notes:
        st.warning(n)
    if p.changes:
        st.dataframe(pd.DataFrame([{
            "요청": c["request_id"], "대상자": c["beneficiary_id"], "바뀜": c["kind"],
            "전": f"{c['worker_before']} {_hhmm(c['arrive_before'])}" if c["worker_before"] else "-",
            "후": f"{c['worker_after']} {_hhmm(c['arrive_after'])}" if c["worker_after"] else "-",
        } for c in p.changes]), hide_index=True, width="stretch")
        people = sorted({c["beneficiary_id"] for c in p.changes})
        staff = sorted({w for c in p.changes for w in (c["worker_before"], c["worker_after"]) if w})
        st.markdown(f"**담당자가 연락·확인할 곳**: 어르신 {len(people)}명({', '.join(people)}), "
                    f"인력 {len(staff)}명({', '.join(staff) or '-'}). 앱은 알림을 보내지 않습니다.")
    if p.refill_reason:
        drafts = [q for q in store.requests() if q["service_date"] == date and q["status"] in ("draft", "needs_review")]
        msg = f"빈자리를 채우지 못했습니다: {REFILL_REASON_KO[p.refill_reason]}."
        if drafts:
            msg += f" 자료 확인 전인 요청이 {len(drafts)}건 있습니다(자료 부족 — '자료 불러오기'·'요청 카드 검토'에서 확인하면 후보가 됩니다)."
        st.info(msg)
    if p.violations:
        st.dataframe(pd.DataFrame(p.violations), hide_index=True, width="stretch")
    if p.ok:
        a1, a2, a3 = st.columns([1, 1, 2])
        actor = a3.text_input("처리자", key="event_actor", placeholder="담당자 이름")
        if a1.button("이대로 적용", type="primary", disabled=saved["eid"] != eid):
            if not actor.strip():
                st.error("처리자를 입력해 주세요.")
            else:
                orig = {r["request_id"]: r for r in inst.requests}
                res = store.apply_event(date, saved["ev"], p, original_requests=orig,
                                        expected_version=saved["version"], actor=actor.strip())
                st.session_state["event_msg"] = (res["status"], res["message"])
                st.session_state.pop(prop_key, None)
                st.rerun()
        if a2.button("적용 안 함"):
            st.session_state.pop(prop_key, None)
            st.rerun()

if st.session_state.get("event_msg"):
    status, msg = st.session_state.pop("event_msg")
    (st.success if status == "applied" else st.warning)(msg)

# ── 기록 ─────────────────────────────────────────────────────────────────
st.divider()
st.subheader(f"{date} 취소·지연 기록")
evs = store.events(date)
if not evs:
    st.caption("아직 등록한 취소·지연이 없습니다.")
else:
    st.dataframe(pd.DataFrame([{
        "접수 시각": _hhmm(e["received_min"]), "실제 발생": _hhmm(e["occurred_min"]),
        "종류": KIND_KO.get(e["kind"], e["kind"]), "요청": e["request_id"],
        "늦어진 시간(분)": e["minutes"] or "", "사유": e["reason_code"], "처리자": e["actor"],
        "바뀐 일정": len(e["result"].get("changes", [])),
        "기록 시각": e["received_at"][:16].replace("T", " "),
    } for e in evs]), hide_index=True, width="stretch")
st.caption("같은 이벤트를 다시 적용하면 한 번만 반영됩니다. 30일 동안 취소·지연이 생기는 상황의 효과는 "
           "'하루 배정 비교' 아래 '취소·지연이 생겼을 때(30일 재생 실험)'에서 봅니다.")
