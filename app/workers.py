"""인력·근무 (W04) — 인력별 수행 서비스·자격 유효기간·근무·휴게, 날짜별 휴무·근무 변경, 근무표 CSV 가져오기.

PRD FR-16(인력 ID·수행 서비스·자격 유효기간·이동수단·권역·근무·휴게), FR-17(근무표 가져오기·변경분,
동행 가능을 가사 가능으로 자동 확장 금지). 검수: 자격 만료·근무시간 밖 요청은 후보 제외(planner.data.apply_worker_settings).
협약기관(FR-18·19)은 후속 단계라 없다. 바뀐 설정이 이미 확정된 일정과 맞지 않으면 조용히 바꾸지 않고 보여준다.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from intake.import_csv import file_hash
from intake.import_shifts import convert_shift_rows, parse_shifts_csv
from store import Store
from store.impact import confirmed_conflicts

DATA = ROOT / "data" / "processed"
SAMPLE = ROOT / "fixtures" / "import" / "sample_shifts.csv"
SKILL_KO = {"hospital_escort": "병원동행", "household_support": "가사지원"}
TRANSPORT = ["자동차", "대중교통", "도보"]

store = Store()

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 인력은 모두 가상입니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("인력·근무")
st.caption("인력이 맡을 수 있는 서비스, 자격 유효기간, 근무·휴게 시간을 관리합니다. 바꾼 값은 다음 계산부터 쓰입니다. "
           "이미 확정된 일정과 맞지 않게 되면 아래에 알려 드리고, 확정 일정은 자동으로 바꾸지 않습니다.")


def _hhmm(m) -> str:
    return "" if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def _t(m: int) -> dt.time:
    return dt.time(int(m) // 60, int(m) % 60)


def _mins(t: dt.time) -> int:
    return t.hour * 60 + t.minute


@st.cache_data(show_spinner=False)
def _base_workers() -> pd.DataFrame:
    return pd.read_csv(DATA / "workers.csv", dtype={"worker_id": str})


@st.cache_data(show_spinner=False)
def _dates() -> list[str]:
    from planner.data import service_dates
    return service_dates(DATA)


def _profile_of(row, profiles: dict) -> dict:
    """저장된 설정이 있으면 그것, 없으면 기본 자료에서 만든 설정."""
    if row.worker_id in profiles:
        return {k: v for k, v in profiles[row.worker_id].items() if not k.startswith("_")}
    return {"skills": [{"code": s.strip(), "valid_to": None} for s in str(row.skills).split(",") if s.strip()],
            "shift_start_min": int(row.shift_start_min), "shift_end_min": int(row.shift_end_min),
            "break_start_min": int(row.break_start_min), "break_minutes": int(row.break_minutes),
            "max_work_minutes": int(row.max_work_minutes), "active": True, "transport_modes": ["자동차"], "region": ""}


def _show_conflicts(dates: list[str]) -> None:
    if not dates:
        return
    bad = confirmed_conflicts(store, DATA, dates)
    if bad:
        st.warning(f"바뀐 조건과 맞지 않는 확정 일정이 {len(bad)}건 있습니다. 자동으로 바꾸지 않았습니다. "
                   "'취소·지연 대응' 또는 '하루 배정 비교'의 확정 해제로 처리해 주세요.")
        st.dataframe(pd.DataFrame([{"날짜": b["service_date"], "요청": b["request_id"], "인력": b["worker_id"],
                                    "내용": b["message"]} for b in bad]), hide_index=True, width="stretch")
    else:
        st.caption("이미 확정된 일정은 바뀐 조건에서도 규칙을 지킵니다.")


base = _base_workers()
profiles = store.worker_profiles()

# ── 인력 목록 ────────────────────────────────────────────────────────────
st.subheader("인력 목록")
rows = []
for r in base.itertuples():
    p = _profile_of(r, profiles)
    rows.append({
        "인력": r.worker_id,
        "수행 서비스(자격 기한)": ", ".join(SKILL_KO.get(s["code"], s["code"]) + (f"(~{s['valid_to']})" if s.get("valid_to") else "")
                                    for s in p["skills"]) or "-",
        "근무": f"{_hhmm(p['shift_start_min'])}~{_hhmm(p['shift_end_min'])}",
        "휴게": f"{_hhmm(p['break_start_min'])}부터 {p['break_minutes']}분",
        "하루 최대(분)": p["max_work_minutes"],
        "상태": "활동" if p.get("active", True) else "중지",
        "이동수단": ", ".join(p.get("transport_modes") or []),
        "권역": p.get("region") or "",
        "설정": f"변경됨({profiles[r.worker_id]['_actor']})" if r.worker_id in profiles else "기본 자료",
    })
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
st.caption("이동시간은 자동차 기준으로만 계산합니다. 이동수단·권역은 기록용이며 아직 배정 계산에 쓰지 않습니다. "
           "출발지는 이동시간 자료와 묶여 있어 여기서 바꾸지 않습니다.")

# ── 인력 한 명 고치기 ────────────────────────────────────────────────────
st.subheader("인력 설정 고치기")
wid = st.selectbox("인력", list(base["worker_id"]), key="edit_worker")
row = next(r for r in base.itertuples() if r.worker_id == wid)
p = _profile_of(row, profiles)
have = {s["code"]: s for s in p["skills"]}
with st.form(f"worker_form_{wid}"):
    st.markdown("**수행 서비스와 자격 기한** — 체크한 서비스만 맡깁니다(동행 가능을 가사 가능으로 넓히지 않습니다).")
    new_skills = []
    for code, label in SKILL_KO.items():
        c1, c2, c3 = st.columns([1, 1, 1])
        on = c1.checkbox(label, value=code in have, key=f"sk_{wid}_{code}")
        limited = c2.checkbox("기한 있음", value=bool(have.get(code, {}).get("valid_to")), key=f"lim_{wid}_{code}")
        vt = have.get(code, {}).get("valid_to")
        until = c3.date_input("자격 기한(이날까지)", value=dt.date.fromisoformat(vt) if vt else dt.date(2026, 12, 31),
                              key=f"until_{wid}_{code}")
        if on:
            new_skills.append({"code": code, "valid_to": until.isoformat() if limited else None})
    c1, c2, c3, c4 = st.columns(4)
    s_start = c1.time_input("근무 시작", value=_t(p["shift_start_min"]), step=600)
    s_end = c2.time_input("근무 끝", value=_t(p["shift_end_min"]), step=600)
    b_start = c3.time_input("휴게 시작", value=_t(p["break_start_min"]), step=600)
    b_len = c4.number_input("휴게(분)", min_value=0, max_value=180, value=int(p["break_minutes"]), step=10)
    c5, c6, c7 = st.columns(3)
    max_work = c5.number_input("하루 최대 근무(분)", min_value=60, max_value=720, value=int(p["max_work_minutes"]), step=30)
    transport = c6.multiselect("이동수단(기록용)", TRANSPORT, default=[t for t in p.get("transport_modes") or [] if t in TRANSPORT])
    region = c7.text_input("권역(기록용)", value=p.get("region") or "")
    active = st.checkbox("활동 중", value=p.get("active", True), help="끄면 모든 날짜의 배정 후보에서 빠집니다.")
    actor = st.text_input("처리자", placeholder="담당자 이름")
    submitted = st.form_submit_button("저장", type="primary")
if submitted:
    new = {"skills": new_skills, "shift_start_min": _mins(s_start), "shift_end_min": _mins(s_end),
           "break_start_min": _mins(b_start), "break_minutes": int(b_len), "max_work_minutes": int(max_work),
           "active": active, "transport_modes": transport, "region": region.strip()}
    try:
        store.save_worker_profile(wid, new, actor=actor.strip())
    except ValueError as e:
        st.error(str(e))
    else:
        st.success(f"{wid} 설정을 저장했습니다. 다음 계산부터 쓰입니다.")
        _show_conflicts(store.confirmed_dates(wid))

# ── 날짜별 휴무·근무 변경 ────────────────────────────────────────────────
st.subheader("날짜별 휴무·근무 변경")
dates = _dates()
d1, d2 = st.columns(2)
s_date = d1.selectbox("날짜", dates, key="shift_date")
s_wid = d2.selectbox("인력", list(base["worker_id"]), key="shift_worker")
with st.form(f"shift_form_{s_date}_{s_wid}"):
    off = st.checkbox("이날 휴무")
    cur = store.day_shifts(s_date).get(s_wid) or {}
    sp = _profile_of(next(r for r in base.itertuples() if r.worker_id == s_wid), profiles)
    c1, c2, c3, c4 = st.columns(4)
    ds = c1.time_input("근무 시작", value=_t(cur.get("start_min", sp["shift_start_min"])), step=600)
    de = c2.time_input("근무 끝", value=_t(cur.get("end_min", sp["shift_end_min"])), step=600)
    dbs = c3.time_input("휴게 시작", value=_t(cur.get("break_start_min", sp["break_start_min"])), step=600)
    dbl = c4.number_input("휴게(분)", min_value=0, max_value=180, value=int(cur.get("break_minutes", sp["break_minutes"])),
                          step=10)
    s_actor = st.text_input("처리자", placeholder="담당자 이름", key="shift_actor")
    s_sub = st.form_submit_button("이날 근무 저장")
if s_sub:
    row_in = {"worker_id": s_wid, "service_date": s_date, "off": off}
    if not off:
        row_in.update(start_min=_mins(ds), end_min=_mins(de), break_start_min=_mins(dbs), break_minutes=int(dbl))
    try:
        sm = store.upsert_shifts([row_in], actor=s_actor.strip(), source="화면")
    except ValueError as e:
        st.error(str(e))
    else:
        st.success(f"저장했습니다(새로 {sm['new']}건, 변경 {sm['changed']}건, 같음 {sm['unchanged']}건).")
        _show_conflicts([s_date])

shifts = store.all_shifts()
if shifts:
    st.dataframe(pd.DataFrame([{
        "날짜": x["service_date"], "인력": x["worker_id"],
        "근무": "휴무" if x["off"] else f"{_hhmm(x['start_min'])}~{_hhmm(x['end_min'])}",
        "휴게": "" if x["off"] else f"{_hhmm(x['break_start_min'])}부터 {x['break_minutes']}분",
        "출처": x["source"], "처리자": x["actor"],
    } for x in shifts]), hide_index=True, width="stretch")
    with st.expander("날짜별 근무 지우기(기본 근무로 되돌림)"):
        pick = st.selectbox("지울 항목", [f"{x['service_date']} {x['worker_id']}" for x in shifts], key="del_shift")
        del_actor = st.text_input("처리자", key="del_actor", placeholder="담당자 이름")
        if st.button("지우기"):
            if not del_actor.strip():
                st.error("처리자를 입력해 주세요.")
            else:
                dd, ww = pick.split(" ")
                store.delete_shift(ww, dd, actor=del_actor.strip())
                st.rerun()

# ── 근무표 CSV ───────────────────────────────────────────────────────────
st.subheader("근무표 CSV 가져오기")
st.caption("열: worker_external_id, date, start_time, end_time, break_start, break_end. 시작·끝을 비우면 그날 휴무입니다. "
           "같은 날짜·인력은 새 값으로 바뀌고, 바뀐 건수를 보여줍니다. 자격은 근무표로 바꾸지 않습니다.")
src = st.radio("가져올 방법", ["CSV 파일 올리기", "예시 근무표(가상)"], horizontal=True, key="shift_src")
data, fname = None, None
if src == "CSV 파일 올리기":
    up = st.file_uploader("근무표 CSV (UTF-8)", type=["csv"], key="shift_up")
    if up is not None:
        data, fname = up.getvalue(), up.name
else:
    data, fname = SAMPLE.read_bytes(), SAMPLE.name
if data is not None:
    parsed = parse_shifts_csv(data, fname)
    if parsed.error:
        st.error(parsed.error)
    else:
        res = convert_shift_rows(parsed.rows, DATA)
        ok = [r.row for r in res if r.row]
        st.dataframe(pd.DataFrame([{
            "줄": r.line, "결과": "오류" if r.errors else "정상",
            "인력": r.row["worker_id"] if r.row else "", "날짜": r.row["service_date"] if r.row else "",
            "근무": ("휴무" if r.row["off"] else f"{_hhmm(r.row['start_min'])}~{_hhmm(r.row['end_min'])}") if r.row else "",
            "내용": " / ".join(r.errors),
        } for r in res]), hide_index=True, width="stretch")
        c_actor = st.text_input("처리자", key="csv_shift_actor", placeholder="담당자 이름")
        if st.button("정상 행 가져오기", disabled=not ok, key=f"apply_shifts_{file_hash(data)[:8]}"):
            if not c_actor.strip():
                st.error("처리자를 입력해 주세요.")
            else:
                sm = store.upsert_shifts(ok, actor=c_actor.strip(), source=f"CSV {fname}")
                st.success(f"근무 {len(ok)}행: 새로 {sm['new']}건, 변경 {sm['changed']}건, 같음 {sm['unchanged']}건. "
                           f"오류 {len(res) - len(ok)}행은 가져오지 않았습니다.")
                _show_conflicts(sorted({r["service_date"] for r in ok}))
