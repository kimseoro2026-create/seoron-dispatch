"""운영 데이터 저장소(SQLite) — PRD 7장(상태 전이·승인 트랜잭션), 8장(데이터 모델).

무엇을 저장하나
  - card_reviews : 요청 카드 처리 이력(AI 값 / 담당자 확정 값 / 처리자). 원문은 저장하지 않고 해시만 둔다.
  - requests     : 운영 중 추가된 요청(지금은 확인 완료 카드). 상태 draft → needs_review → ready → cancelled/closed.
                   f0 가상 권역의 기본 요청은 data/processed/requests.csv 에 그대로 두고 여기 복사하지 않는다.
  - planning_runs: 계산 실행 기록(입력 스냅샷 해시·엔진 버전·설정·상태). 같은 입력이면 같은 실행 ID.
  - assignments  : 담당자가 결정한 배정(confirmed / rejected / cancelled / superseded / released).
                   한 요청에 활성 확정은 하나만(부분 unique 색인).
  - schedule_versions, idempotency, audit_logs.

승인 규칙은 planner.approval.ApprovalGate 와 같다(멱등 키 → version 확인 → 재검증 → 잠금 → 반영).
다른 점은 version 을 날짜마다 따로 두고, 확정을 그날 확정끼리만 함께 검증한다는 것이다.

정직하게: 단일 기관·단일 담당자(M2 범위)용이다. 권한(JWT·RLS)은 없다. 배포 서버(Streamlit Cloud)의 파일은
앱을 다시 켜면 처음 상태로 돌아간다(가상 데이터 시연용).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from planner.approval import (ACCEPT, PENDING, REJECT, STATUS_APPROVED, STATUS_COMPLETE,
                              STATUS_CONFLICT, STATUS_REJECTED_ALL, STATUS_STALE,
                              ApprovalError, ApprovalResult, Decision)

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = 1

# 카드 처리 → 요청 상태 (PRD 7장)
CARD_ACTION_STATUS = {"확인 완료": "ready", "보류": "needs_review", "취소": "cancelled"}
# 배정 조건 비교에서 뺄 필드: 확인할 때마다 바뀌지만 배정 조건은 아니다
_NOT_CONDITION = {"requested_at"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS card_reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_key TEXT NOT NULL, card_id TEXT NOT NULL,
  text_hash TEXT NOT NULL, extracted_json TEXT NOT NULL, final_json TEXT NOT NULL,
  action TEXT NOT NULL, reason TEXT NOT NULL, reviewer TEXT NOT NULL,
  request_id TEXT, reviewed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_card_reviews_key ON card_reviews(card_key, id);
CREATE TABLE IF NOT EXISTS requests(
  request_id TEXT PRIMARY KEY, service_date TEXT NOT NULL, source TEXT NOT NULL,
  status TEXT NOT NULL, payload_json TEXT NOT NULL, version INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_requests_date ON requests(service_date, status);
CREATE TABLE IF NOT EXISTS planning_runs(
  run_id TEXT PRIMARY KEY, service_date TEXT NOT NULL, algorithm TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL, engine_version TEXT NOT NULL, config_json TEXT NOT NULL,
  status TEXT NOT NULL, solve_seconds REAL, metrics_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignments(
  assignment_id TEXT PRIMARY KEY, run_id TEXT, request_id TEXT NOT NULL, worker_id TEXT NOT NULL,
  service_date TEXT NOT NULL, arrive_min INTEGER NOT NULL, status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', approval_version INTEGER, actor TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_assign_active ON assignments(request_id) WHERE status = 'confirmed';
CREATE INDEX IF NOT EXISTS ix_assign_date ON assignments(service_date, status);
CREATE TABLE IF NOT EXISTS schedule_versions(service_date TEXT PRIMARY KEY, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, service_date TEXT,
  before_version INTEGER, after_version INTEGER, detail_json TEXT NOT NULL, at TEXT NOT NULL);
"""


def default_store_path() -> Path:
    """SEORON_DB_PATH 가 있으면 그 파일, 없으면 data/store/seoron.db (git 제외)."""
    env = os.environ.get("SEORON_DB_PATH")
    return Path(env) if env else ROOT / "data" / "store" / "seoron.db"


def _now() -> str:
    return dt.datetime.now(KST).isoformat(timespec="seconds")


def _dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, default=str)


_ENGINE_VERSION: str | None = None


def engine_version() -> str:
    """엔진·검증기 소스의 해시. 코드가 바뀌면 값이 바뀌어, 어떤 코드로 계산했는지 남는다."""
    global _ENGINE_VERSION
    if _ENGINE_VERSION is None:
        h = hashlib.sha256()
        for d in ("planner", "verifier"):
            for p in sorted((ROOT / d).glob("*.py")):
                h.update(p.name.encode())
                h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        _ENGINE_VERSION = "src-" + h.hexdigest()[:12]
    return _ENGINE_VERSION


def snapshot_hash(instance: dict) -> str:
    """입력 스냅샷 해시. 이동시간은 dict((from,to)→분)·list 어느 형태든 같은 값이 나오게 정리한다."""
    travel = instance.get("travel") or {}
    if isinstance(travel, dict):
        tl = sorted([str(a), str(b), float(m)] for (a, b), m in travel.items())
    else:
        tl = sorted([str(t["from_node"]), str(t["to_node"]), float(t["minutes"])] for t in travel)
    body = {"workers": sorted(instance.get("workers", []), key=lambda w: str(w.get("worker_id"))),
            "requests": sorted(instance.get("requests", []), key=lambda r: str(r.get("request_id"))),
            "travel": tl}
    return hashlib.sha256(_dumps(body).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()          # executescript 는 스스로 커밋하므로 트랜잭션 밖에서 실행
        try:
            con.executescript(_SCHEMA)
            con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))
        finally:
            con.close()

    # ── 연결 ──────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _tx(self, immediate: bool = False):
        """한 트랜잭션. immediate=True 면 시작할 때 쓰기 잠금을 잡아 승인 경합을 줄 세운다."""
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def _audit(self, con, actor, action, entity_type, entity_id, service_date=None,
               before=None, after=None, detail=None) -> None:
        con.execute("INSERT INTO audit_logs(actor, action, entity_type, entity_id, service_date,"
                    " before_version, after_version, detail_json, at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (actor, action, entity_type, entity_id, service_date, before, after,
                     _dumps(detail or {}), _now()))

    def _version(self, con, service_date: str) -> int:
        row = con.execute("SELECT version FROM schedule_versions WHERE service_date=?",
                          (service_date,)).fetchone()
        return int(row["version"]) if row else 0

    def _bump(self, con, service_date: str) -> int:
        v = self._version(con, service_date) + 1
        con.execute("INSERT INTO schedule_versions(service_date, version) VALUES (?, ?) "
                    "ON CONFLICT(service_date) DO UPDATE SET version=excluded.version", (service_date, v))
        return v

    def _end_active(self, con, request_id: str, new_status: str, actor: str, reason: str) -> None:
        """요청의 활성 확정을 끝낸다(무효화·취소). 그 날짜 version 을 올려 오래된 화면의 승인을 막는다."""
        row = con.execute("SELECT * FROM assignments WHERE request_id=? AND status='confirmed'",
                          (request_id,)).fetchone()
        if row is None:
            return
        con.execute("UPDATE assignments SET status=?, reason=?, updated_at=? WHERE assignment_id=?",
                    (new_status, reason, _now(), row["assignment_id"]))
        before = self._version(con, row["service_date"])
        after = self._bump(con, row["service_date"])
        action = {"superseded": "supersede", "cancelled": "cancel"}.get(new_status, new_status)
        self._audit(con, actor, action, "assignment", request_id, row["service_date"], before, after,
                    {"worker_id": row["worker_id"], "reason": reason})

    # ── 요청 카드 ──────────────────────────────────────────────────────────
    def save_card_review(self, card_key: str, *, card_id: str, text_hash: str, extracted: dict,
                         final: dict, action: str, reason: str, reviewer: str,
                         request: dict | None) -> None:
        """카드 처리 한 번을 기록한다. 원문은 받지 않는다(해시만).

        확인 완료 → 요청 ready(배정 계산 대상). 보류 → needs_review. 취소 → cancelled.
        ready 요청의 배정 조건이 바뀌거나 보류·취소되면 관련 확정을 무효화한다(PRD 7장).
        """
        if action not in CARD_ACTION_STATUS:
            raise ValueError(f"알 수 없는 처리: {action}")
        if action != "확인 완료" and not reason.strip():
            raise ValueError("보류·취소 사유가 필요합니다")
        if action == "확인 완료" and not request:
            raise ValueError("확인 완료에는 변환된 요청이 필요합니다")
        now = _now()
        with self._tx(immediate=True) as con:
            prev = con.execute("SELECT request_id FROM card_reviews WHERE card_key=? AND request_id IS NOT NULL"
                               " ORDER BY id DESC LIMIT 1", (card_key,)).fetchone()
            rid = request["request_id"] if request else (prev["request_id"] if prev else None)
            con.execute("INSERT INTO card_reviews(card_key, card_id, text_hash, extracted_json, final_json,"
                        " action, reason, reviewer, request_id, reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (card_key, card_id, text_hash, _dumps(extracted), _dumps(final), action,
                         reason, reviewer, rid, now))
            self._audit(con, reviewer, f"card:{action}", "card", card_key, request.get("service_date")
                        if request else None, detail={"card_id": card_id, "request_id": rid, "reason": reason})
            if rid is None:
                return
            status = CARD_ACTION_STATUS[action]
            old = con.execute("SELECT * FROM requests WHERE request_id=?", (rid,)).fetchone()
            if request is not None:
                if old is None:
                    con.execute("INSERT INTO requests(request_id, service_date, source, status, payload_json,"
                                " version, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                                (rid, request["service_date"], request.get("source", "card"), status,
                                 _dumps(request), now, now))
                    return
                old_payload = json.loads(old["payload_json"])
                changed = ({k: v for k, v in old_payload.items() if k not in _NOT_CONDITION}
                           != {k: v for k, v in json.loads(_dumps(request)).items() if k not in _NOT_CONDITION})
                con.execute("UPDATE requests SET service_date=?, status=?, payload_json=?, version=version+?,"
                            " updated_at=? WHERE request_id=?",
                            (request["service_date"], status, _dumps(request), 1 if changed or
                             old["status"] != status else 0, now, rid))
                if changed:
                    self._end_active(con, rid, "superseded", reviewer, "요청 내용이 바뀌어 다시 승인이 필요합니다")
            elif old is not None:
                con.execute("UPDATE requests SET status=?, version=version+1, updated_at=? WHERE request_id=?",
                            (status, now, rid))
                self._end_active(con, rid, "cancelled" if status == "cancelled" else "superseded",
                                 reviewer, reason)

    def card_review(self, card_key: str) -> dict | None:
        h = self.card_history(card_key)
        return h[-1] if h else None

    def card_history(self, card_key: str) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM card_reviews WHERE card_key=? ORDER BY id", (card_key,)).fetchall()
        finally:
            con.close()
        return [{"card_id": r["card_id"], "text_hash": r["text_hash"], "action": r["action"],
                 "reason": r["reason"], "reviewer": r["reviewer"], "request_id": r["request_id"],
                 "reviewed_at": r["reviewed_at"], "extracted": json.loads(r["extracted_json"]),
                 "final": json.loads(r["final_json"])} for r in rows]

    def card_review_keys(self) -> list[str]:
        """처리 기록이 있는 카드 키(최근 처리 순)."""
        con = self._connect()
        try:
            rows = con.execute("SELECT card_key, MAX(id) AS m FROM card_reviews GROUP BY card_key"
                               " ORDER BY m DESC").fetchall()
        finally:
            con.close()
        return [r["card_key"] for r in rows]

    def ready_requests(self, service_date: str | None = None) -> list[dict]:
        con = self._connect()
        try:
            if service_date is None:
                rows = con.execute("SELECT payload_json FROM requests WHERE status='ready'"
                                   " ORDER BY service_date, request_id").fetchall()
            else:
                rows = con.execute("SELECT payload_json FROM requests WHERE status='ready' AND service_date=?"
                                   " ORDER BY request_id", (service_date,)).fetchall()
        finally:
            con.close()
        return [json.loads(r["payload_json"]) for r in rows]

    def ready_request_dates(self) -> list[str]:
        con = self._connect()
        try:
            rows = con.execute("SELECT DISTINCT service_date FROM requests WHERE status='ready'"
                               " ORDER BY service_date").fetchall()
        finally:
            con.close()
        return [r["service_date"] for r in rows]

    def request_status(self, request_id: str) -> str | None:
        con = self._connect()
        try:
            row = con.execute("SELECT status FROM requests WHERE request_id=?", (request_id,)).fetchone()
        finally:
            con.close()
        return row["status"] if row else None

    # ── 계산 실행 기록 (FR-23) ─────────────────────────────────────────────
    def record_run(self, service_date: str, algorithm: str, instance: dict, *, config: dict,
                   status: str, solve_seconds: float | None, metrics: dict) -> str:
        """실행을 기록하고 실행 ID 를 돌려준다. 같은 날짜·방식·입력·설정·엔진이면 같은 ID(처음 기록 유지)."""
        snap = snapshot_hash(instance)
        eng = engine_version()
        run_id = "run-" + hashlib.sha256(_dumps([service_date, algorithm, snap, eng, config]).encode()).hexdigest()[:16]
        with self._tx() as con:
            con.execute("INSERT OR IGNORE INTO planning_runs(run_id, service_date, algorithm, snapshot_hash,"
                        " engine_version, config_json, status, solve_seconds, metrics_json, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run_id, service_date, algorithm, snap, eng, _dumps(config), status,
                         solve_seconds, _dumps(metrics), _now()))
        return run_id

    def run(self, run_id: str) -> dict | None:
        con = self._connect()
        try:
            r = con.execute("SELECT * FROM planning_runs WHERE run_id=?", (run_id,)).fetchone()
        finally:
            con.close()
        if r is None:
            return None
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json"))
        d["metrics"] = json.loads(d.pop("metrics_json"))
        return d

    # ── 승인 (PRD 7장 승인 트랜잭션) ───────────────────────────────────────
    def schedule_version(self, service_date: str) -> int:
        con = self._connect()
        try:
            return self._version(con, service_date)
        finally:
            con.close()

    def confirmed(self, service_date: str) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM assignments WHERE service_date=? AND status='confirmed'"
                               " ORDER BY worker_id, arrive_min", (service_date,)).fetchall()
        finally:
            con.close()
        return [{"request_id": r["request_id"], "worker_id": r["worker_id"], "arrive_min": r["arrive_min"],
                 "service_date": r["service_date"], "run_id": r["run_id"], "actor": r["actor"],
                 "locked": True} for r in rows]

    def approve(self, service_date: str, instance: dict, proposal: list[dict],
                decisions: list[Decision], *, expected_version: int, actor: str = "담당자",
                idempotency_key: str | None = None, run_id: str | None = None) -> ApprovalResult:
        by_pid = {a["request_id"]: a for a in proposal}
        decided: dict[str, Decision] = {}
        for d in decisions:
            if d.request_id not in by_pid:
                raise ApprovalError(f"제안에 없는 요청입니다: {d.request_id}")
            if d.action == REJECT and not d.reason.strip():
                raise ApprovalError(f"거부 사유가 비어 있습니다: {d.request_id}")
            decided[d.request_id] = d

        with self._tx(immediate=True) as con:
            # 2. 멱등 키 — 같은 키면 처음 결과를 그대로 돌려준다
            if idempotency_key:
                row = con.execute("SELECT result_json FROM idempotency WHERE key=?",
                                  (idempotency_key,)).fetchone()
                if row:
                    return ApprovalResult(**json.loads(row["result_json"]))

            def remember(res: ApprovalResult) -> ApprovalResult:
                if idempotency_key:
                    con.execute("INSERT INTO idempotency(key, result_json, created_at) VALUES (?,?,?)",
                                (idempotency_key, _dumps(dataclasses.asdict(res)), _now()))
                return res

            # 3. version 확인
            current = self._version(con, service_date)
            if expected_version != current:
                return remember(ApprovalResult(
                    status=STATUS_STALE, schedule_version=current,
                    message=f"화면의 일정 버전({expected_version})이 최신({current})과 다릅니다. "
                            "새로 계산된 화면에서 다시 결정해 주세요."))

            before = [dict(r) for r in con.execute(
                "SELECT request_id, worker_id, arrive_min FROM assignments WHERE service_date=?"
                " AND status='confirmed'", (service_date,)).fetchall()]
            locked_ids = {r["request_id"] for r in before}
            accepted = [{"request_id": a["request_id"], "worker_id": a["worker_id"],
                         "arrive_min": int(a["arrive_min"])}
                        for a in proposal
                        if a["request_id"] in decided and decided[a["request_id"]].action == ACCEPT]

            # 잠금 — 이미 확정된 요청은 덮어쓰지 않는다
            for a in accepted:
                if a["request_id"] in locked_ids:
                    return remember(ApprovalResult(
                        status=STATUS_CONFLICT, schedule_version=current,
                        confirmed=self._confirmed_con(con, service_date),
                        message=f"{a['request_id']} 는 이미 확정된 일정입니다(잠금). "
                                "바꾸려면 먼저 확정을 해제해 주세요."))

            # 4. 재검증 — 그날 확정 + 이번 수락을 함께
            from verifier import verify
            merged = before + accepted
            rep = verify(instance, merged) if merged else None
            if rep is not None and rep.violations:
                return remember(ApprovalResult(
                    status=STATUS_CONFLICT, schedule_version=current,
                    confirmed=self._confirmed_con(con, service_date),
                    violations=[v.as_dict() for v in rep.violations],
                    message="확정된 일정과 충돌합니다. 반영하지 않았습니다."))

            # 5. 반영
            after = current + 1
            now = _now()
            for a in accepted:
                con.execute("INSERT INTO assignments(assignment_id, run_id, request_id, worker_id, service_date,"
                            " arrive_min, status, approval_version, actor, created_at, updated_at)"
                            " VALUES (?,?,?,?,?,?,'confirmed',?,?,?,?)",
                            (uuid.uuid4().hex, run_id, a["request_id"], a["worker_id"], service_date,
                             a["arrive_min"], after, actor, now, now))
                self._audit(con, actor, ACCEPT, "assignment", a["request_id"], service_date, current, after,
                            {"worker_id": a["worker_id"], "arrive_min": a["arrive_min"], "run_id": run_id})
            rejected, pending = [], []
            for a in proposal:
                d = decided.get(a["request_id"])
                if d is None or d.action == PENDING:
                    pending.append(dict(a))
                elif d.action == REJECT:
                    rejected.append({**a, "reason": d.reason})
                    con.execute("INSERT INTO assignments(assignment_id, run_id, request_id, worker_id,"
                                " service_date, arrive_min, status, reason, approval_version, actor,"
                                " created_at, updated_at) VALUES (?,?,?,?,?,?,'rejected',?,?,?,?,?)",
                                (uuid.uuid4().hex, run_id, a["request_id"], a["worker_id"], service_date,
                                 int(a["arrive_min"]), d.reason, after, actor, now, now))
                    self._audit(con, actor, REJECT, "assignment", a["request_id"], service_date, current,
                                after, {"worker_id": a["worker_id"], "reason": d.reason, "run_id": run_id})
            self._bump(con, service_date)

            if not accepted and rejected and not pending:
                status, msg = STATUS_REJECTED_ALL, "모든 제안을 거부했습니다. 배정은 확정되지 않았습니다."
            elif pending:
                status, msg = STATUS_APPROVED, f"{len(accepted)}건 확정, {len(pending)}건 대기. 대기 건은 아직 확정이 아닙니다."
            else:
                status, msg = STATUS_COMPLETE, f"{len(accepted)}건 확정, {len(rejected)}건 거부."
            return remember(ApprovalResult(status=status, schedule_version=after,
                                           confirmed=self._confirmed_con(con, service_date),
                                           rejected=rejected, pending=pending, message=msg))

    def _confirmed_con(self, con, service_date: str) -> list[dict]:
        rows = con.execute("SELECT request_id, worker_id, arrive_min FROM assignments WHERE service_date=?"
                           " AND status='confirmed' ORDER BY worker_id, arrive_min", (service_date,)).fetchall()
        return [dict(r) for r in rows]

    def release(self, service_date: str, request_id: str, *, actor: str = "담당자", reason: str = "") -> None:
        """확정을 해제한다. 자동으로 풀리지 않고, 사유가 있어야 한다(PRD 6.3-6)."""
        if not reason.strip():
            raise ApprovalError("확정 해제 사유가 필요합니다")
        with self._tx(immediate=True) as con:
            row = con.execute("SELECT 1 FROM assignments WHERE request_id=? AND service_date=? AND status='confirmed'",
                              (request_id, service_date)).fetchone()
            if row is None:
                raise ApprovalError(f"확정된 일정이 없습니다: {request_id}")
            self._end_active(con, request_id, "released", actor, reason)

    def reset_date(self, service_date: str, *, actor: str = "담당자") -> None:
        """실험용: 그날 확정을 모두 해제한다. 기록은 지우지 않고 '초기화'로 남긴다."""
        with self._tx(immediate=True) as con:
            n = con.execute("UPDATE assignments SET status='released', reason='확정 초기화', updated_at=?"
                            " WHERE service_date=? AND status='confirmed'", (_now(), service_date)).rowcount
            before = self._version(con, service_date)
            after = self._bump(con, service_date)
            self._audit(con, actor, "reset", "schedule", service_date, service_date, before, after,
                        {"released": n})

    # ── 감사 기록 ──────────────────────────────────────────────────────────
    def audit(self, limit: int = 200) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM audit_logs ORDER BY id LIMIT ?", (limit,)).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            out.append(d)
        return out
