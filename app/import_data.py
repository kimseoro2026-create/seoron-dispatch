"""자료 불러오기 (W02) — CSV → 열 이름 맞추기 → 미리보기 → 검토 전(draft) 요청 → 담당자 확인.

PRD FR-05(CSV·붙여넣기·예시 자료, 열 매핑·미리보기), FR-07(10MB·확장자·인코딩 검사),
FR-08(같은 파일·같은 요청번호 중복 안내, 변경은 담당자 확인 뒤에만), FR-09(정상·오류 행 수와 위치, 정상 행도 draft).
파일 원문은 저장하지 않는다(파일 지문만). 메모 원문 붙여넣기는 '요청 카드 검토' 화면에서 한다.
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

from intake.import_csv import (REQUIRED, TARGETS, convert_rows, file_hash, parse_csv_bytes,
                               suggest_mapping)
from store import Store

DATA = ROOT / "data" / "processed"
SAMPLE = ROOT / "fixtures" / "import" / "sample_requests.csv"
SERVICE_KO = {"hospital_escort": "병원동행", "household_support": "가사지원"}
STATUS_KO = {"draft": "검토 전", "needs_review": "보류(검토 필요)", "ready": "확인 완료",
             "cancelled": "취소", "closed": "종료"}
NONE = "(연결 안 함)"

store = Store()

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 실제 어르신 정보를 올리지 마세요. 올린 파일의 원문은 저장하지 않습니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("자료 불러오기")
st.caption("기관에서 쓰던 요청 목록(CSV)을 한꺼번에 가져옵니다. 가져온 요청은 **'검토 전'**으로만 들어가고, "
           "담당자가 '확인 완료'를 눌러야 '하루 배정 비교' 계산에 들어갑니다.")

c1, c2, c3 = st.columns(3)
c1.info("**① 자료 고르기**\n\nCSV 파일, 붙여넣기, 예시 자료")
c2.info("**② 열 이름 맞추기 · 미리보기**\n\n틀린 행은 줄 번호와 이유를 보여줍니다")
c3.info("**③ 가져오기 → 확인 완료**\n\n확인한 요청만 배정 계산에 들어갑니다")

# ── ① 자료 고르기 ─────────────────────────────────────────────────────────
st.subheader("① 자료 고르기")
mode = st.radio("가져올 방법", ["CSV 파일 올리기", "CSV 내용 붙여넣기", "예시 자료(가상)"], horizontal=True)
data, fname, kind = None, None, None
if mode == "CSV 파일 올리기":
    up = st.file_uploader("CSV 파일 (UTF-8, 10MB 이하)", type=["csv"])
    if up is not None:
        data, fname, kind = up.getvalue(), up.name, "csv"
elif mode == "CSV 내용 붙여넣기":
    txt = st.text_area("첫 줄은 열 이름, 쉼표로 구분 (가상 내용만)", height=160,
                       placeholder="요청번호,대상자 ID,서비스 종류,서비스일,시작 가능 시각,늦어도 시작할 시각,서비스 시간(분)\n"
                                   "A-1,B0010,가사지원,2026-11-24,09:30,10:30,120")
    if txt.strip():
        data, fname, kind = txt.encode("utf-8"), "붙여넣기.csv", "paste"
else:
    data, fname, kind = SAMPLE.read_bytes(), SAMPLE.name, "sample"
    st.caption("예시 자료는 일부러 틀린 행(없는 날짜, 등록 안 된 대상자)과 빈 우선순위를 넣어 두었습니다.")

with st.expander("CSV 열 안내"):
    st.markdown("\n".join(
        f"- **{label}**{' *' if t in REQUIRED else ''} — 예: `{aliases[0]}`, `{aliases[1]}`"
        for t, (label, aliases) in TARGETS.items()))
    st.caption("* 꼭 필요한 열. 가사지원은 '서비스 시간(분)', 병원동행은 병원(ID 또는 이름)·외래 예약 시각·병원 체류(분)가 "
               "더 필요합니다. 날짜는 2026-11-24, 시각은 13:10 형식. "
               "'늦어도 끝낼 시각'은 '늦어도 시작할 시각'과 뜻이 달라 자동으로 연결하지 않습니다.")

if data is None:
    st.stop()

parsed = parse_csv_bytes(data, fname)
if parsed.error:
    st.error(parsed.error)
    st.stop()
fhash = file_hash(data)
before = store.find_import_by_hash(fhash)
if before:
    st.warning(f"같은 파일을 {before['created_at'][:16].replace('T', ' ')}에 가져온 적이 있습니다 "
               f"(결과: 새 요청 {before['n_new']}건). 다시 가져와도 같은 요청번호·같은 내용은 새로 만들지 않습니다.")

# ── ② 열 이름 맞추기 · 미리보기 ──────────────────────────────────────────
st.subheader("② 열 이름 맞추기 · 미리보기")
st.caption(f"'{fname}' · {len(parsed.rows)}행 · 열 {len(parsed.headers)}개. 자동으로 짐작한 연결이 틀리면 고쳐 주세요.")
guess = suggest_mapping(parsed.headers)
mapping = {}
cols = st.columns(3)
for i, (t, (label, _)) in enumerate(TARGETS.items()):
    opts = [NONE] + parsed.headers
    default = opts.index(guess[t]) if t in guess else 0
    choice = cols[i % 3].selectbox(label + (" *" if t in REQUIRED else ""), opts, index=default,
                                   key=f"map_{fhash[:8]}_{t}")
    if choice != NONE:
        mapping[t] = choice
dup_cols = {c for c in mapping.values() if list(mapping.values()).count(c) > 1}
if dup_cols:
    st.error("한 열을 두 항목에 연결했습니다: " + ", ".join(sorted(dup_cols)))
    st.stop()
unused = [h for h in parsed.headers if h not in mapping.values()]
if unused:
    st.caption("가져오지 않는 열: " + ", ".join(unused))

now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
results = convert_rows(parsed.rows, mapping, DATA, imported_at=now)
n_ok = sum(1 for r in results if r.request)
n_err = len(results) - n_ok
m1, m2, m3 = st.columns(3)
m1.metric("전체 행", len(results))
m2.metric("가져올 수 있는 행", n_ok)
m3.metric("오류 행", n_err)
prev = pd.DataFrame([{
    "줄": r.line, "요청번호": r.external_id,
    "결과": "오류" if r.errors else ("경고" if r.warnings else "정상"),
    "서비스": SERVICE_KO.get(r.request["service_type"], "") if r.request else "",
    "서비스일": r.request["service_date"] if r.request else "",
    "내용": " / ".join(r.errors + r.warnings),
} for r in results])
st.dataframe(prev, hide_index=True, width="stretch")
st.caption("오류 행은 가져오지 않습니다. 파일을 고친 뒤 다시 올리면 같은 요청번호의 정상 행은 중복으로 만들지 않습니다.")

# ── ③ 가져오기 ───────────────────────────────────────────────────────────
a1, a2 = st.columns([1, 2])
actor = a2.text_input("처리자", placeholder="담당자 이름 또는 ID", key="import_actor")
if a1.button("가져오기 (검토 전 요청으로 저장)", type="primary", disabled=n_ok == 0):
    if not actor.strip():
        st.error("처리자를 입력해 주세요.")
    else:
        rows = [{"line": r.line, "external_id": r.external_id, "request": r.request,
                 "errors": r.errors, "warnings": r.warnings} for r in results]
        st.session_state["last_import"] = store.import_requests(
            file_hash=fhash, file_name=fname, kind=kind, actor=actor.strip(), mapping=mapping, rows=rows)

last = st.session_state.get("last_import")
if last:
    msg = (f"새 요청 {last['n_new']}건을 '검토 전'으로 저장했습니다. 변경 없는 중복 {last['n_unchanged']}건, "
           f"내용이 바뀐 요청 {last['n_changed']}건, 오류 {last['n_error']}건.")
    (st.success if last["status"] == "succeeded" else st.warning)(msg)
    if last["changed"]:
        st.markdown("**내용이 바뀐 요청** — 체크한 것만 반영합니다. 반영하면 다시 '검토 전'이 되고, 확정된 배정은 풀립니다.")
        ch = pd.DataFrame([{"반영": False, "요청": c["request_id"], "바뀐 항목": ", ".join(c["fields"]),
                            "지금 상태": STATUS_KO.get(c["status"], c["status"])} for c in last["changed"]])
        ch_ed = st.data_editor(ch, hide_index=True, width="stretch", disabled=["요청", "바뀐 항목", "지금 상태"],
                               key=f"changes_{last['import_id']}")
        if st.button("체크한 변경 반영"):
            ids = list(ch_ed.loc[ch_ed["반영"], "요청"])
            if not ids:
                st.info("반영할 요청을 체크해 주세요.")
            else:
                n = store.apply_import_changes(last["import_id"], ids, actor=actor.strip() or "담당자")
                st.success(f"{n}건을 반영했습니다. 아래 '검토 대기 요청'에서 다시 확인해 주세요.")
                last["changed"] = [c for c in last["changed"] if c["request_id"] not in ids]

# ── 검토 대기 요청 → 확인 완료 ───────────────────────────────────────────
st.divider()
st.subheader("검토 대기 요청")
st.caption("가져온 요청을 확인하고 '확인 완료'를 누르면 그 날짜의 '하루 배정 비교' 계산에 들어갑니다. "
           "보류·취소는 사유가 필요합니다.")
waiting = [r for r in store.requests(source="import") if r["status"] in ("draft", "needs_review")]
if not waiting:
    st.info("검토 대기 중인 가져온 요청이 없습니다.")
else:
    def _hhmm(m):
        return "" if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"

    wdf = pd.DataFrame([{
        "선택": False, "요청": r["request_id"], "상태": STATUS_KO[r["status"]],
        "대상자": r["payload"]["beneficiary_id"], "서비스": SERVICE_KO[r["payload"]["service_type"]],
        "서비스일": r["service_date"],
        "시작 가능~늦어도": f"{_hhmm(r['payload']['earliest_start_min'])}~{_hhmm(r['payload']['latest_start_min'])}",
        "예약": _hhmm(r["payload"].get("appointment_min")),
        "우선순위": r["payload"]["priority"],
    } for r in waiting])
    if st.button("모두 선택"):
        st.session_state["select_all_waiting"] = True
    if st.session_state.get("select_all_waiting"):
        wdf["선택"] = True
    w_ed = st.data_editor(wdf, hide_index=True, width="stretch",
                          disabled=[c for c in wdf.columns if c != "선택"],
                          key=f"waiting_{len(waiting)}_{st.session_state.get('select_all_waiting', False)}")
    r1, r2, r3 = st.columns([1, 1, 2])
    action = r1.radio("처리", ["확인 완료", "보류", "취소"], key="waiting_action")
    reviewer = r2.text_input("처리자", key="waiting_reviewer", placeholder="담당자 이름")
    reason = r3.text_input("사유 (보류·취소 시 필수)", key="waiting_reason")
    if st.button("선택한 요청 처리", type="primary"):
        ids = list(w_ed.loc[w_ed["선택"], "요청"])
        if not ids:
            st.error("처리할 요청을 선택해 주세요.")
        elif not reviewer.strip():
            st.error("처리자를 입력해 주세요.")
        elif action != "확인 완료" and not reason.strip():
            st.error("보류·취소 사유를 입력해 주세요.")
        else:
            n = store.review_requests(ids, action, actor=reviewer.strip(), reason=reason.strip())
            st.session_state.pop("select_all_waiting", None)
            st.session_state["review_msg"] = (f"{n}건을 '{action}'(으)로 처리했습니다."
                                              + (" '하루 배정 비교'에서 그 날짜를 고르면 계산에 들어갑니다."
                                                 if action == "확인 완료" else ""))
            st.rerun()
if st.session_state.get("review_msg"):
    st.success(st.session_state.pop("review_msg"))

ready = [r for r in store.requests(source="import") if r["status"] == "ready"]
if ready:
    dates = sorted({r["service_date"] for r in ready})
    st.caption(f"확인 완료된 가져온 요청 {len(ready)}건 · 날짜: " + ", ".join(dates))

# ── 가져오기 기록 ─────────────────────────────────────────────────────────
with st.expander("가져오기 기록"):
    imps = store.imports()
    if not imps:
        st.caption("아직 가져온 자료가 없습니다.")
    else:
        status_ko = {"succeeded": "성공", "partial": "일부 성공", "failed": "실패"}
        st.dataframe(pd.DataFrame([{
            "시각": i["created_at"][:16].replace("T", " "), "파일": i["file_name"],
            "결과": status_ko.get(i["status"], i["status"]), "행": i["n_rows"], "새 요청": i["n_new"],
            "중복": i["n_unchanged"], "변경": i["n_changed"], "오류": i["n_error"], "처리자": i["actor"],
        } for i in imps]), hide_index=True, width="stretch")
        err_rows = [{"파일": i["file_name"], "줄": e["line"], "요청번호": e["external_id"],
                     "이유": " / ".join(e["errors"])} for i in imps for e in i["errors"]]
        if err_rows:
            st.markdown("**오류 행**")
            st.dataframe(pd.DataFrame(err_rows), hide_index=True, width="stretch")
    st.caption("파일 원문은 저장하지 않습니다(파일 지문만). 배포 화면은 앱을 다시 켜면 기록이 처음 상태로 돌아갑니다.")
