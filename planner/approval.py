"""담당자 승인 흐름 — PRD 9장 '승인 트랜잭션', 6.3-6 '확정 일정 잠금'.

원칙(CLAUDE.md 6절):
  AI는 판정하지 않는다. 배정 확정은 항상 담당자가 한다.
  이 모듈은 '제안'과 '확정'을 분리하고, 사람의 결정 없이는 어떤 배정도 확정되지 않게 한다.

트랜잭션 순서(PRD 9장):
  1. 권한·기관 검사     — M2는 단일 기관·단일 담당자라 생략(주석으로 남긴다)
  2. idempotency_key 중복 확인
  3. schedule_version 확인 (오래된 제안은 반영하지 않는다)
  4. 최신 자료로 시간·자격·휴게·중복 재검증
  5. 승인된 배정 저장, version 증가
  6. 승인 대기 슬롯은 유지 — 담당자가 명시적으로 처리할 때까지 점유된 것으로 본다

정직하게: 이 모듈은 메모리 안에서 상태를 들고 있다.
  실제 서비스에서는 DB 트랜잭션과 RPC 로 옮겨야 한다(docs/STATUS.md 에 남긴다).
"""
from __future__ import annotations

from dataclasses import dataclass, field

ACCEPT = "accept"
REJECT = "reject"
PENDING = "pending"

# 승인 결과 상태
STATUS_APPROVED = "approved"          # 일부 확정됨
STATUS_COMPLETE = "completed"         # 전부 확정
STATUS_REJECTED_ALL = "rejected_all"  # 전부 거부
STATUS_CONFLICT = "conflict"          # 재검증 실패로 반영하지 않음
STATUS_STALE = "stale"                # schedule_version 불일치


class ApprovalError(RuntimeError):
    """승인 트랜잭션이 거부됐을 때."""


@dataclass(frozen=True)
class Decision:
    """담당자가 한 요청에 대해 내린 결정."""
    request_id: str
    action: str                      # ACCEPT / REJECT / PENDING
    reason: str = ""                 # 거부 사유(거부일 때 필수)


@dataclass
class ApprovalResult:
    status: str
    schedule_version: int = 0
    confirmed: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_APPROVED, STATUS_COMPLETE, STATUS_REJECTED_ALL)


@dataclass
class _Entry:
    """확정된 배정 한 건. 잠금 여부를 함께 들고 있다."""
    request_id: str
    worker_id: str
    arrive_min: int
    service_date: str
    locked: bool = True


class ApprovalGate:
    """하루치 제안을 받아 담당자 결정을 적용하고 확정 일정을 관리한다."""

    def __init__(self) -> None:
        self._confirmed: dict[str, _Entry] = {}
        self._version: int = 0
        self._audit: list[dict] = []
        self._idempotent: dict[str, ApprovalResult] = {}

    # ── 조회 ──────────────────────────────────────────────────────────────
    @property
    def schedule_version(self) -> int:
        return self._version

    @property
    def confirmed(self) -> list[dict]:
        return [{"request_id": e.request_id, "worker_id": e.worker_id,
                 "arrive_min": e.arrive_min, "service_date": e.service_date,
                 "locked": e.locked} for e in self._confirmed.values()]

    @property
    def audit(self) -> list[dict]:
        return list(self._audit)

    # ── 승인 ──────────────────────────────────────────────────────────────
    def approve(self, instance: dict, proposal: list[dict],
                decisions: list[Decision], *, schedule_version: int,
                actor: str = "담당자", idempotency_key: str | None = None,
                service_date: str = "") -> ApprovalResult:
        """제안에 담당자 결정을 적용한다.

        instance: 검증에 쓸 입력(요청·인력·이동시간)
        proposal: [{request_id, worker_id, arrive_min}]
        decisions: [Decision]
        schedule_version: 담당자가 화면에서 본 version. 현재와 다르면 반영하지 않는다.
        """
        # 2. idempotency — 같은 키로 다시 부르면 이전 결과를 그대로 돌려준다(중복 확정 방지)
        if idempotency_key and idempotency_key in self._idempotent:
            return self._idempotent[idempotency_key]

        # 3. version 확인 — 오래된 제안이 최신 확정을 덮어쓰지 못하게 한다
        if schedule_version != self._version:
            return self._remember(idempotency_key, ApprovalResult(
                status=STATUS_STALE, schedule_version=self._version,
                message=f"제안 version {schedule_version} 이 현재 {self._version} 과 다릅니다. "
                        f"화면을 새로고침한 뒤 다시 결정해 주세요."))

        by_pid = {a["request_id"]: a for a in proposal}
        decided: dict[str, Decision] = {}
        for d in decisions:
            if d.request_id not in by_pid:
                raise ApprovalError(f"제안에 없는 요청입니다: {d.request_id}")
            if d.action == REJECT and not d.reason.strip():
                raise ApprovalError(f"거부 사유가 비어 있습니다: {d.request_id}")
            decided[d.request_id] = d

        # 4. 재검증 — 확정된 기존 일정 + 이번에 수락한 배정을 함께 본다
        confirmed_before = [{"request_id": e.request_id, "worker_id": e.worker_id,
                             "arrive_min": e.arrive_min}
                            for e in self._confirmed.values()]
        accepted = [
            {"request_id": a["request_id"], "worker_id": a["worker_id"],
             "arrive_min": int(a["arrive_min"])}
            for a in proposal
            if decided.get(a["request_id"]) and decided[a["request_id"]].action == ACCEPT
        ]

        from verifier import verify
        merged = confirmed_before + accepted
        rep = verify(instance, merged) if merged else None
        if rep is not None and rep.violations:
            return self._remember(idempotency_key, ApprovalResult(
                status=STATUS_CONFLICT, schedule_version=self._version,
                confirmed=self.confirmed,
                violations=[v.as_dict() for v in rep.violations],
                message="확정된 일정과 충돌합니다. 반영하지 않았습니다."))

        # 이미 확정된 요청은 덮어쓰지 않는다(잠금)
        for a in accepted:
            existing = self._confirmed.get(a["request_id"])
            if existing and existing.locked:
                return self._remember(idempotency_key, ApprovalResult(
                    status=STATUS_CONFLICT, schedule_version=self._version,
                    confirmed=self.confirmed,
                    message=f"{a['request_id']} 는 이미 확정된 일정입니다(잠금). "
                            f"변경하려면 담당자가 먼저 잠금을 해제해야 합니다."))

        # 5. 반영
        newly = []
        for a in accepted:
            entry = _Entry(request_id=a["request_id"], worker_id=a["worker_id"],
                           arrive_min=int(a["arrive_min"]), service_date=service_date)
            self._confirmed[a["request_id"]] = entry
            newly.append({"request_id": entry.request_id, "worker_id": entry.worker_id,
                          "arrive_min": entry.arrive_min})
            self._audit.append({"actor": actor, "action": ACCEPT,
                                "request_id": entry.request_id,
                                "worker_id": entry.worker_id,
                                "version_before": self._version,
                                "version_after": self._version + 1})

        rejected = []
        pending = []
        for a in proposal:
            d = decided.get(a["request_id"])
            if d is None or d.action == PENDING:
                pending.append(dict(a))
            elif d.action == REJECT:
                rejected.append({**a, "reason": d.reason})
                self._audit.append({"actor": actor, "action": REJECT,
                                    "request_id": a["request_id"],
                                    "reason": d.reason,
                                    "version_before": self._version,
                                    "version_after": self._version + 1})

        self._version += 1

        if not newly and rejected and not pending:
            status = STATUS_REJECTED_ALL
            message = "모든 제안을 거부했습니다. 배정은 확정되지 않았습니다."
        elif pending:
            status = STATUS_APPROVED
            message = f"{len(newly)}건 확정, {len(pending)}건 대기. 대기 건은 아직 확정이 아닙니다."
        else:
            status = STATUS_COMPLETE
            message = f"{len(newly)}건 확정, {len(rejected)}건 거부."

        return self._remember(idempotency_key, ApprovalResult(
            status=status, schedule_version=self._version,
            confirmed=self.confirmed, rejected=rejected, pending=pending,
            message=message))

    # ── 잠금 해제(담당자 명시 조작) ───────────────────────────────────────
    def release(self, request_id: str, *, actor: str = "담당자",
                reason: str = "") -> None:
        """확정된 배정을 잠금 해제한다. 자동으로 풀리지 않는다(PRD 6.3-6)."""
        entry = self._confirmed.pop(request_id, None)
        if entry is None:
            raise ApprovalError(f"확정된 일정이 없습니다: {request_id}")
        if not reason.strip():
            raise ApprovalError("잠금 해제 사유가 필요합니다")
        self._version += 1
        self._audit.append({"actor": actor, "action": "release",
                            "request_id": request_id, "reason": reason,
                            "version_before": self._version - 1,
                            "version_after": self._version})

    def reset(self) -> None:
        """실험용 초기화. 감사 이력도 지운다."""
        self._confirmed.clear()
        self._version = 0
        self._audit.clear()
        self._idempotent.clear()

    def _remember(self, key: str | None, result: ApprovalResult) -> ApprovalResult:
        if key:
            self._idempotent[key] = result
        return result
