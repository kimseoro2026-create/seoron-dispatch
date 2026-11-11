"""요청 카드 정확도 평가 — 정답 세트(fixtures/request_cards/cases.json)와 비교.

지표(모두 계산값):
  value_accuracy     정답 값이 있는 필드 중 맞게 뽑은 비율
  wrong_value        값을 냈는데 틀린 수(누락보다 위험: 담당자가 놓치기 쉽다)
  missed             정답이 있는데 비운 수
  invented           정답이 null인데 값을 낸 수
  flag_recall        '확인 필요'여야 하는 필드 중 확인 필요로 표시한 비율
  unsafe_ready       카드가 '검토 필요 없음(ready)'인데 배정 필수 값이 틀린 카드 수 ← 가장 중요한 안전 지표
  flag_false_alarm   값을 맞게 뽑았고 확인이 필요 없는 필드에 '확인 필요'를 붙인 수(담당자 일을 늘린다)
  over_review        배정 필수 값이 모두 맞고 검토가 필요 없는 카드인데 '검토 필요'로 보낸 카드 수
                     (값을 못 뽑아서 검토로 간 카드는 여기에 넣지 않는다 — 그건 누락·틀린 값으로 센다)
  mobility_judgment  이동능력을 원문 밖에서 판정한 시도 수(검사가 막은 수)

실행:
  python -m intake.evaluate --method rules              # 규칙 추출(모의), 호출 0건
  python -m intake.evaluate --method llm                # .env 모델로 실제 호출(저장 결과는 재사용)
  python -m intake.evaluate --method llm --model claude-haiku --limit 5
  python -m intake.evaluate --method llm --cases holdout --prompt card-v1
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .extract import PROMPT_VERSION, ExtractResult, extract_card
from .schema import BLOCKING_BY_SERVICE, FIELDS, FIELD_LABELS
from .synth import DEFAULT_OUT, HOLDOUT_OUT

CASE_SETS = {"dev": DEFAULT_OUT, "holdout": HOLDOUT_OUT}

OUT_DIR = Path("data/experiments")
_TEXT_FIELDS = {n for n, (_, k) in FIELDS.items() if k == "text"}


def values_match(name: str, pred, gold) -> bool:
    if pred is None or gold is None:
        return pred is None and gold is None
    if name in _TEXT_FIELDS:  # 근거 구간을 조금 넓게/좁게 잡는 차이는 허용(원문 표현 포함 관계)
        p, g = str(pred).strip().rstrip("."), str(gold).strip()
        return g in p or p in g if p else False
    return pred == gold


def score_case(case: dict, res: ExtractResult) -> list[dict]:
    rows = []
    gold_service = case["gold"]["service_type"]["value"]
    blocking = set(BLOCKING_BY_SERVICE[gold_service])
    for name, g in case["gold"].items():
        f = res.check.fields[name]
        p = f["value"]
        rows.append({
            "case_id": case["case_id"], "variant": case["variant"], "field": name,
            "blocking": name in blocking, "gold": g["value"], "pred": p,
            "correct": values_match(name, p, g["value"]),
            "gold_flag": g["needs_confirmation"], "pred_flag": bool(f["needs_confirmation"]),
            "card_status": res.status,
        })
    return rows


def summarize(rows: pd.DataFrame, results: dict[str, ExtractResult]) -> dict:
    has_gold = rows[rows.gold.notna()]
    no_gold = rows[rows.gold.isna()]
    wrong = has_gold[(~has_gold.correct) & has_gold.pred.notna()]
    missed = has_gold[has_gold.pred.isna()]
    invented = no_gold[no_gold.pred.notna()]
    flagged = rows[rows.gold_flag]

    unsafe = over = need_review_cases = 0
    for cid, g in rows.groupby("case_id"):
        status = g.card_status.iloc[0]
        b = g[g.blocking]
        expected_review = bool((b.gold_flag | b.gold.isna()).any())
        need_review_cases += expected_review
        if status == "ready" and not b.correct.all():
            unsafe += 1
        if status == "needs_review" and not expected_review and b.correct.all():
            over += 1
    false_alarm = rows[(~rows.gold_flag) & rows.pred_flag & rows.correct]

    statuses = defaultdict(int)
    for r in results.values():
        statuses[r.status] += 1
    mobility = sum(1 for r in results.values() for i in r.check.issues if i["code"] == "MOBILITY_JUDGMENT")

    by_field = {}
    for name, g in has_gold.groupby("field"):
        by_field[name] = {"n": int(len(g)), "accuracy": round(float(g.correct.mean()), 3)}
    by_variant = {}
    for v, g in has_gold.groupby("variant"):
        by_variant[v] = {"n_fields": int(len(g)), "accuracy": round(float(g.correct.mean()), 3),
                         "cases": int(g.case_id.nunique())}
    inj = rows[rows.variant == "injection"]

    lat = [r.latency_s for r in results.values() if r.source == "live"]
    return {
        "cases": len(results),
        "value_accuracy": round(float(has_gold.correct.mean()), 3) if len(has_gold) else None,
        "blocking_accuracy": round(float(has_gold[has_gold.blocking].correct.mean()), 3),
        "fields_with_gold": int(len(has_gold)),
        "wrong_value": int(len(wrong)),
        "missed": int(len(missed)),
        "invented": int(len(invented)),
        "flag_recall": round(float(flagged.pred_flag.mean()), 3) if len(flagged) else None,
        "flags_expected": int(len(flagged)),
        "unsafe_ready": unsafe,
        "flag_false_alarm": int(len(false_alarm)),
        "flag_false_alarm_blocking": int(false_alarm.blocking.sum()),
        "false_alarm_fields": {k: int(v) for k, v in false_alarm.field.value_counts().items()},
        "over_review": over,
        "cases_needing_review": need_review_cases,
        "mobility_judgment": mobility,
        "injection_accuracy": round(float(inj[inj.gold.notna()].correct.mean()), 3) if len(inj) else None,
        "status_counts": dict(statuses),
        "by_field": by_field,
        "by_variant": by_variant,
        "live_calls": len(lat),
        "cache_hits": sum(1 for r in results.values() if r.source == "cache"),
        "latency_mean_s": round(sum(lat) / len(lat), 2) if lat else None,
        "input_tokens": sum(r.input_tokens or 0 for r in results.values() if r.source == "live"),
        "output_tokens": sum(r.output_tokens or 0 for r in results.values() if r.source == "live"),
    }


def load_cases(path: str | Path = DEFAULT_OUT) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def run(method: str, model: str | None = None, limit: int | None = None, allow_live: bool = True,
        case_set: str = "dev", prompt_version: str = PROMPT_VERSION, out_dir: Path = OUT_DIR,
        save: bool = True) -> dict:
    cases = load_cases(CASE_SETS[case_set])[: limit or None]
    results: dict[str, ExtractResult] = {}
    rows: list[dict] = []
    for i, c in enumerate(cases, 1):
        res = extract_card(c["text"], method, model=model, allow_live=allow_live, prompt_version=prompt_version)
        results[c["case_id"]] = res
        rows.extend(score_case(c, res))
        mark = res.error or res.status
        print(f"[{i}/{len(cases)}] {c['case_id']} {c['variant']:<14} {res.source:<5} {mark}", flush=True)
    df = pd.DataFrame(rows)
    used_model = next((r.model for r in results.values() if r.model), None)
    summary = {
        "method": method, "model": used_model if method == "llm" else None,
        "source_label": "규칙 추출(모의)" if method == "rules" else "AI 추출",
        "prompt_version": prompt_version if method == "llm" else None,
        "case_set": case_set,
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        **summarize(df, results),
    }
    if save:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        tag = (method if method == "rules" else f"llm-{used_model or 'unknown'}-{prompt_version}") + f"-{case_set}"
        df.to_csv(out_dir / f"cards-{tag}-{stamp}.csv", index=False, encoding="utf-8-sig")
        (out_dir / f"cards-summary-{tag}-{stamp}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        summary["saved_as"] = f"cards-summary-{tag}-{stamp}.json"
    return summary


def print_summary(s: dict) -> None:
    print()
    print(f"방식: {s['source_label']}  모델: {s['model'] or '-'}  프롬프트: {s['prompt_version'] or '-'}  "
          f"사례: {s['case_set']} {s['cases']}건")
    print(f"값 정확도 {s['value_accuracy']}  (배정 필수 {s['blocking_accuracy']})  "
          f"틀린 값 {s['wrong_value']} · 누락 {s['missed']} · 지어낸 값 {s['invented']}")
    print(f"확인 필요 표시 재현율 {s['flag_recall']} ({s['flags_expected']}개 중)  "
          f"위험한 '검토 필요 없음' 카드 {s['unsafe_ready']}  이동능력 판정 시도 {s['mobility_judgment']}")
    print(f"불필요한 '확인 필요' {s['flag_false_alarm']}개 (배정 필수 {s['flag_false_alarm_blocking']})  "
          f"검토 불필요한데 검토로 보낸 카드 {s['over_review']} (검토가 필요한 카드 {s['cases_needing_review']}건)")
    print(f"카드 상태 {s['status_counts']}  실시간 호출 {s['live_calls']} · 저장 결과 {s['cache_hits']}")
    print("유형별:", {k: v["accuracy"] for k, v in s["by_variant"].items()})
    weak = {FIELD_LABELS[k]: v["accuracy"] for k, v in s["by_field"].items() if v["accuracy"] < 1}
    if weak:
        print("100% 아닌 필드:", weak)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="요청 카드 정확도 평가")
    ap.add_argument("--method", choices=["rules", "llm"], default="rules")
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cases", choices=list(CASE_SETS), default="dev", help="dev: 기존 40건, holdout: 새 40건")
    ap.add_argument("--prompt", default=PROMPT_VERSION, help="프롬프트 버전(card-v1, card-v2)")
    ap.add_argument("--no-live", action="store_true", help="저장 결과만 사용(호출 안 함)")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    s = run(a.method, model=a.model, limit=a.limit, allow_live=not a.no_live, case_set=a.cases,
            prompt_version=a.prompt, save=not a.no_save)
    print_summary(s)
    if s.get("saved_as"):
        print(f"저장: data/experiments/{s['saved_as']}")


if __name__ == "__main__":
    main()
