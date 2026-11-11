"""요청 카드 검토 페이지 (PRD W03).

요청 원문(상담 메모)을 요청 카드로 정리하고, 담당자가 항목별로 확인·수정한다.
- 원문과 항목·근거를 나란히 보여준다. 근거 구간은 원문에서 노란색으로 표시한다.
- 추출 방식을 항상 표시한다: AI 추출(실시간) / AI 추출(저장된 결과) / 규칙 추출(모의).
- AI는 자격·의료 필요·자립보행을 판정하지 않는다. 확인은 담당자가 한다.
- 정확도 숫자는 data/experiments/cards-summary-*.json(평가 스크립트 계산값)에서 읽는다.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from adapters import llm
from intake.extract import PROMPT_VERSION, extract_card
from intake.schema import BLOCKING_BY_SERVICE, BLOCKING_COMMON, FIELD_LABELS, FIELDS, check_format

CASES_PATH = ROOT / "fixtures/request_cards/cases.json"
CACHE_DIR = ROOT / llm.DEFAULT_CACHE_DIR
EXP_DIR = ROOT / "data/experiments"
VARIANT_LABEL = {
    "basic": "표준 메모", "no_year": "연도 없는 날짜", "missing_window": "시간 정보 없음",
    "injection": "원문 속 지시문", "discharge": "퇴원 예정·실제 퇴원", "phrasing": "말 표현(정오, 두 시간)",
    "freeform": "양식 없는 메모",
}
ACTIONS = ("확인 완료", "보류", "취소")

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 요청 원문은 가상 요청에서 만든 예시 메모입니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("요청 카드 검토")
st.caption("상담 메모를 배정에 필요한 항목으로 정리합니다. AI는 정리만 하고, **확인은 담당자가 합니다.** "
           "확인 완료된 카드만 배정 대상이 됩니다.")


@st.cache_data
def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


@st.cache_data
def _cached_models(n_files: int) -> list[str]:  # n_files 는 캐시 키(파일이 늘면 다시 읽음)
    """저장된 AI 결과가 있는 모델 목록(현재 프롬프트 버전 기준)."""
    models = set()
    if CACHE_DIR.exists():
        for p in CACHE_DIR.glob("*.json"):
            try:
                e = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if e.get("prompt_version") == PROMPT_VERSION:
                models.add(e.get("requested_model") or e.get("model"))
    return sorted(m for m in models if m)


def _n_cache_files() -> int:
    return len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0


# ── 사이드바: 추출 방식 ───────────────────────────────────────────────────
cfg = llm.load_config(ROOT / ".env")
saved_models = _cached_models(_n_cache_files())
# 기본값은 사용량을 쓰지 않는 방식(저장된 결과 → 규칙 추출). 실시간 호출은 맨 뒤에 둔다.
options = [f"AI 추출 · 저장된 결과 ({m})" for m in saved_models]
options.append("규칙 추출 (모의)")
if cfg:
    options.append(f"AI 추출 · 실시간 호출 ({cfg.model})")

with st.sidebar:
    st.header("추출 방식")
    choice = st.radio("방식", options, label_visibility="collapsed")
    if choice.startswith("규칙"):
        st.info("정규식 규칙으로 뽑은 **모의 결과**입니다. 실제 AI 결과가 아닙니다.")
    elif "실시간" in choice:
        st.warning("실시간 호출은 AI 사용량을 씁니다. 같은 메모는 저장된 결과를 다시 씁니다.")
    elif not cfg:
        st.caption("AI 키가 없는 환경이라 저장된 결과만 보여줍니다.")

method = "rules" if choice.startswith("규칙") else "llm"
model = None
if method == "llm":
    model = choice.split("(")[-1].rstrip(")")
allow_live = "실시간" in choice

# ── 메모 고르기 ───────────────────────────────────────────────────────────
cases = _cases()
labels = [f"{c['case_id']} · {VARIANT_LABEL.get(c['variant'], c['variant'])}" for c in cases]
col_pick, col_free = st.columns([2, 1])
with col_pick:
    idx = st.selectbox("예시 메모", range(len(cases) + 1),
                       format_func=lambda i: labels[i] if i < len(cases) else "직접 입력")
case = cases[idx] if idx < len(cases) else None
if case is None:
    text = st.text_area("상담 메모를 붙여넣으세요 (가상 내용만)", height=180,
                        placeholder="[상담 메모] 접수 2026-11-10 10:00\n대상자 B0001 어르신 가사지원 요청. ...")
else:
    text = case["text"]

if not text.strip():
    st.stop()

key = f"{method}|{model}|{llm.sha256(text)}"
results = st.session_state.setdefault("card_results", {})
if key not in results:
    if allow_live:
        if st.button("AI로 정리하기", type="primary"):
            with st.spinner("AI가 메모를 정리하는 중…"):
                results[key] = extract_card(text, "llm", model=model, allow_live=True,
                                            cache_dir=CACHE_DIR, log_path=ROOT / llm.DEFAULT_LOG_PATH)
        else:
            st.info("‘AI로 정리하기’를 누르면 정리합니다(저장된 결과가 있으면 그것을 씁니다).")
            st.stop()
    else:
        results[key] = extract_card(text, method, model=model, allow_live=False, cache_dir=CACHE_DIR)
res = results[key]

# ── 결과 상단 상태 ────────────────────────────────────────────────────────
status_text = {
    "ready": ("배정 필수 항목이 모두 채워졌습니다. 담당자 확인 후 배정 대상이 됩니다.", "success"),
    "needs_review": ("확인이 필요한 항목이 있습니다. 표시된 항목을 확인·수정해 주세요.", "warning"),
    "schema_error": ("추출 결과 형식이 맞지 않아 사용하지 않았습니다. 직접 입력해 주세요.", "error"),
    "llm_error": (f"AI 호출 실패: {res.error}. 값을 채우지 않았습니다. 직접 입력하거나 규칙 추출을 고르세요.", "error"),
}[res.status]
getattr(st, status_text[1])(f"**{res.source_label}** — {status_text[0]}")


def _highlight(src: str, spans: list[str]) -> str:
    out = html.escape(src)
    for s in sorted({s for s in spans if s}, key=len, reverse=True):
        e = html.escape(s)
        if e in out:
            out = out.replace(e, f"<mark style='background:#fff59d;color:#222;padding:0 2px'>{e}</mark>", 1)
    return out.replace("\n", "<br>")


fields = res.check.fields
service = fields["service_type"]["value"]
blocking = BLOCKING_BY_SERVICE.get(service, BLOCKING_COMMON)

left, right = st.columns([1, 1.4])
with left:
    st.markdown("**원문** (노란색 = 근거로 쓴 구간)")
    st.markdown(
        f"<div style='border:1px solid #ddd;border-radius:6px;padding:12px;font-size:0.9rem;line-height:1.7'>"
        f"{_highlight(text, [f['evidence_span'] for f in fields.values()])}</div>",
        unsafe_allow_html=True,
    )
    if res.check.issues:
        st.markdown("**검사 결과**")
        for i in res.check.issues:
            name = FIELD_LABELS.get(i["field"], "카드 전체") if i["field"] else "카드 전체"
            st.markdown(f"- {name}: {i['message']}")

with right:
    st.markdown("**요청 카드** — 값이 틀리면 ‘수정값’에 고쳐 쓰고, 확인한 항목은 ‘확인함’을 체크하세요.")
    rows = []
    for name, f in fields.items():
        if service == "household_support" and name in ("hospital_name", "appointment_time", "hospital_stay_minutes") \
                and f["value"] is None:
            continue
        if service == "hospital_escort" and name == "service_minutes" and f["value"] is None:
            continue
        if name not in blocking and f["value"] is None and not f["needs_confirmation"]:
            continue  # 원문에 없는 선택 항목(퇴원일 등)은 줄을 만들지 않는다
        need = f["needs_confirmation"] or (name in blocking and f["value"] is None)
        rows.append({
            "key": name,
            "항목": FIELD_LABELS[name] + (" *" if name in blocking else ""),
            "추출값": "" if f["value"] is None else str(f["value"]),
            "수정값": "",
            "상태": "⚠️ 확인 필요" if need else "정상",
            "확인함": False,
            "근거": f["evidence_span"] or "",
        })
    df = pd.DataFrame(rows).set_index("key")
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key=f"editor|{key}",
        disabled=["항목", "추출값", "근거", "상태"],
        column_config={
            "항목": st.column_config.TextColumn(width="medium"),
            "추출값": st.column_config.TextColumn(width="small"),
            "수정값": st.column_config.TextColumn(width="small"),
            "상태": st.column_config.TextColumn(width="small"),
            "확인함": st.column_config.CheckboxColumn(width="small"),
            "근거": st.column_config.TextColumn(width="medium"),
        },
    )
    st.caption("* 배정에 꼭 필요한 항목. 형식: 날짜 2026-11-02, 시각 13:10, 시간(분) 120")

    # 최종값 계산과 확인 완료 차단 사유(FR-57)
    final, problems = {}, []
    for name, row in edited.iterrows():
        val = row["수정값"].strip() if row["수정값"] else row["추출값"]
        kind = FIELDS[name][1]
        parsed = (int(val) if val.isdigit() else val) if (kind == "minutes" and val) else (val or None)
        if row["수정값"] and not check_format(name, parsed):
            problems.append(f"{FIELD_LABELS[name]}: 형식이 맞지 않습니다")
        final[name] = parsed
        if name in blocking:
            if not parsed:
                problems.append(f"{FIELD_LABELS[name]}: 값이 없습니다")
            elif row["상태"] != "정상" and not row["수정값"] and not row["확인함"]:
                problems.append(f"{FIELD_LABELS[name]}: 확인이 필요합니다")

    c1, c2 = st.columns([1, 2])
    with c1:
        action = st.radio("처리", ACTIONS, horizontal=False)
    with c2:
        reviewer = st.text_input("처리자", placeholder="담당자 이름 또는 ID")
        reason = st.text_input("사유 (보류·취소 시 필수)")
    reviews = st.session_state.setdefault("card_reviews", {})
    if st.button("처리 적용", type="primary"):
        if not reviewer.strip():
            st.error("처리자를 입력해 주세요.")
        elif action == "확인 완료" and problems:
            st.error("확인 완료할 수 없습니다:\n\n" + "\n".join(f"- {p}" for p in problems))
        elif action != "확인 완료" and not reason.strip():
            st.error("보류·취소 사유를 입력해 주세요.")
        else:
            edits = {n: {"추출값": edited.loc[n, "추출값"], "수정값": edited.loc[n, "수정값"]}
                     for n in edited.index if edited.loc[n, "수정값"]}
            reviews[key] = {"처리": action, "처리자": reviewer.strip(), "사유": reason.strip(),
                            "시각": dt.datetime.now().strftime("%H:%M:%S"), "추출 방식": res.source_label,
                            "수정": edits, "최종값": final}
    if key in reviews:
        r = reviews[key]
        st.success(f"{r['시각']} · {r['처리자']} · **{r['처리']}**" + (f" (사유: {r['사유']})" if r["사유"] else "")
                   + (f" · 수정 {len(r['수정'])}개 항목" if r["수정"] else ""))
        st.caption("이 화면의 처리 기록은 시연용으로 브라우저 세션에만 남습니다.")

if case is not None:
    with st.expander("정답과 비교 (평가용 정답 세트)"):
        cmp_rows = []
        for name, g in case["gold"].items():
            p = fields[name]["value"]
            if g["value"] is None and p is None:
                continue
            cmp_rows.append({"항목": FIELD_LABELS[name], "정답": "" if g["value"] is None else str(g["value"]),
                             "추출": "" if p is None else str(p),
                             "일치": "✅" if (p == g["value"] or (isinstance(p, str) and isinstance(g["value"], str)
                                                                and (g["value"] in p or p in g["value"]))) else "❌"})
        st.dataframe(pd.DataFrame(cmp_rows), hide_index=True, width="stretch")
        st.caption("정답은 원래 가상 요청 값에서 계산했습니다(AI가 만든 정답이 아님).")

# ── 정확도 평가 결과 ──────────────────────────────────────────────────────
st.divider()
st.subheader("정확도 평가 (정답 세트 40건)")


def _latest_summaries() -> list[dict]:
    latest: dict[str, tuple[str, dict]] = {}
    for p in sorted(EXP_DIR.glob("cards-summary-*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        tag = s["source_label"] + (f" ({s['model']})" if s.get("model") else "")
        latest[tag] = (p.name, s)
    return [dict(s, _file=f, _tag=t) for t, (f, s) in latest.items()]


summ = _latest_summaries()
if not summ:
    st.info("아직 평가 결과가 없습니다. `python -m intake.evaluate --method rules` 로 계산합니다.")
else:
    table = []
    for s in summ:
        table.append({
            "방식": s["_tag"],
            "값 정확도": f"{s['value_accuracy']:.1%}",
            "배정 필수 항목": f"{s['blocking_accuracy']:.1%}",
            "틀린 값": s["wrong_value"], "누락": s["missed"], "지어낸 값": s["invented"],
            "‘확인 필요’ 표시": "-" if s["flag_recall"] is None else f"{s['flag_recall']:.0%}",
            "위험한 확인대기 카드": s["unsafe_ready"],
            "불필요하게 검토로 보낸 카드": s.get("over_review", "-"),
            "양식 없는 메모": f"{s['by_variant'].get('freeform', {}).get('accuracy', 0):.1%}",
            "계산 시각": s["run_at"].replace("T", " "),
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    with st.expander("지표 읽는 법과 한계"):
        st.markdown(
            "- **틀린 값**: 값을 냈지만 틀린 항목 수. 비워 둔 것(누락)보다 위험합니다. 담당자가 놓치기 쉽기 때문입니다.\n"
            "- **위험한 확인대기 카드**: 검토 필요 표시 없이 넘어왔는데 배정 필수 값이 틀린 카드 수. 0이어야 합니다.\n"
            "- **‘확인 필요’ 표시**: 연도 없는 날짜·시간 누락처럼 확인이 필요한 항목을 표시한 비율.\n"
            "- **불필요하게 검토로 보낸 카드**: 값이 모두 맞고 확인할 것도 없는데 ‘검토 필요’로 보낸 카드 수. "
            "안전에는 문제가 없지만 담당자 일을 늘립니다.\n"
            "- 규칙 추출(모의)은 예시 메모 양식을 보고 만든 규칙이라 양식이 같은 메모에서는 점수가 높게 나옵니다. "
            "**양식 없는 메모** 열이 양식 밖 표현에 대한 비교입니다.\n"
            "- 정답 세트는 가상 요청에서 만든 40건이며, 실제 기관 메모로 다시 평가해야 합니다."
        )
