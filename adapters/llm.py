"""LLM 호출 어댑터 — 서비스 내부 LLM 호출은 이 파일 한 곳에서만 한다.

- 공급자·주소·키·모델은 .env 설정값(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)이다.
  Anthropic Messages 형식(/v1/messages, Authorization: Bearer)으로 호출한다.
  12월 이후 다른 공급자로 바꿀 때는 설정과 _call_messages 만 바꾸면 된다.
- 같은 (모델, 프롬프트 버전, 입력)은 결과를 저장해 다시 호출하지 않는다(PRD 10.11).
  저장 결과는 source="cache"로 표시하며 실시간 추론으로 표시하지 않는다.
- 호출 기록(모델·프롬프트 버전·처리시간·토큰·오류·입력 해시)을 남긴다(PRD 10.8).
  키와 원문은 기록·오류 메시지에 남기지 않는다.
- 실패는 LLMError로 올린다. 빈 문자열이나 가짜 결과로 바꾸지 않는다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CACHE_DIR = Path("data/llm_cache")
DEFAULT_LOG_PATH = Path("data/llm_logs/calls.jsonl")
ANTHROPIC_VERSION = "2023-06-01"
_RETRY_STATUS = {429, 500, 502, 503, 504, 529}


class LLMError(Exception):
    """LLM 호출 실패. 메시지에 키·원문을 넣지 않는다."""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 90.0
    max_retries: int = 3

    def __repr__(self) -> str:  # 키가 로그·오류에 찍히지 않게 한다
        return f"LLMConfig(base_url={self.base_url!r}, model={self.model!r}, api_key=***)"


@dataclass
class LLMResult:
    text: str
    model: str
    source: str            # live | cache
    prompt_version: str
    input_hash: str
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None


def load_config(env_path: str | Path | None = ".env", model: str | None = None) -> LLMConfig | None:
    """설정을 읽는다. 키·주소·모델 중 하나라도 없으면 None(호출 불가)."""
    if env_path:
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass
    base = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    key = os.getenv("LLM_API_KEY", "").strip()
    mdl = (model or os.getenv("LLM_MODEL", "")).strip()
    if not (base and key and mdl):
        return None
    return LLMConfig(base_url=base, api_key=key, model=mdl)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cache_key(model: str, prompt_version: str, system: str, user: str) -> str:
    return sha256(json.dumps([model, prompt_version, system, user], ensure_ascii=False))


def read_cache(key: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> dict | None:
    p = Path(cache_dir) / f"{key}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _write_cache(key: str, entry: dict, cache_dir: Path) -> None:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")


def _log(entry: dict, log_path: Path | None) -> None:
    if log_path is None:
        return
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _call_messages(cfg: LLMConfig, system: str, user: str, max_tokens: int, temperature: float) -> dict:
    """Anthropic Messages 형식 호출 1회. 재시도할 오류는 (True, 메시지)로 구분해 올린다."""
    import requests

    try:
        r = requests.post(
            f"{cfg.base_url}/v1/messages",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": cfg.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=cfg.timeout_s,
        )
    except requests.RequestException as e:
        raise _Retryable(f"연결 오류({type(e).__name__})") from None
    if r.status_code in _RETRY_STATUS:
        raise _Retryable(f"HTTP {r.status_code}")
    if not r.ok:
        # 응답 본문에 입력이 되돌아올 수 있어 상태 코드와 오류 유형만 남긴다
        etype = ""
        try:
            etype = r.json().get("error", {}).get("type", "")
        except ValueError:
            pass
        raise LLMError(f"HTTP {r.status_code} {etype}".strip())
    return r.json()


class _Retryable(Exception):
    pass


def complete(system: str, user: str, *, prompt_version: str, config: LLMConfig | None = None,
             max_tokens: int = 2000, temperature: float = 0.0, use_cache: bool = True,
             cache_dir: Path = DEFAULT_CACHE_DIR, log_path: Path | None = DEFAULT_LOG_PATH,
             purpose: str = "", model: str | None = None) -> LLMResult:
    """LLM 한 번 호출(또는 저장 결과 재사용). 실패하면 LLMError.

    model 을 주면 설정 모델 대신 그 모델을 쓴다(키 없이 저장 결과만 찾을 때도 쓴다).
    """
    cfg = config or load_config(model=model)
    if cfg is not None and model and cfg.model != model:
        from dataclasses import replace
        cfg = replace(cfg, model=model)
    model = model or (cfg.model if cfg else os.getenv("LLM_MODEL", ""))
    key = cache_key(model, prompt_version, system, user)
    input_hash = sha256(user)

    if use_cache and model:
        hit = read_cache(key, cache_dir)
        if hit is not None:
            return LLMResult(text=hit["text"], model=hit["model"], source="cache",
                             prompt_version=prompt_version, input_hash=input_hash, latency_s=0.0,
                             input_tokens=hit.get("input_tokens"), output_tokens=hit.get("output_tokens"))
    if cfg is None:
        raise LLMError("LLM 설정 없음(.env의 LLM_BASE_URL·LLM_API_KEY·LLM_MODEL)")

    t0 = time.time()
    err: str | None = None
    data: dict | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            data = _call_messages(cfg, system, user, max_tokens, temperature)
            err = None
            break
        except _Retryable as e:
            err = str(e)
            if attempt < cfg.max_retries:
                time.sleep(min(5 * 2 ** attempt, 40))
        except LLMError as e:
            err = str(e)
            break
    latency = round(time.time() - t0, 2)

    usage = (data or {}).get("usage", {}) or {}
    log = {
        "at": dt.datetime.now().isoformat(timespec="seconds"), "purpose": purpose, "model": cfg.model,
        "prompt_version": prompt_version, "input_hash": input_hash, "latency_s": latency,
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "error": err,
    }
    _log(log, log_path)
    if err is not None or data is None:
        raise LLMError(err or "응답 없음")

    text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
    if not text.strip():
        raise LLMError("빈 응답")
    result = LLMResult(text=text, model=data.get("model", cfg.model), source="live",
                       prompt_version=prompt_version, input_hash=input_hash, latency_s=latency,
                       input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
    if use_cache:
        _write_cache(key, {
            "model": result.model, "requested_model": cfg.model, "prompt_version": prompt_version,
            "input_hash": input_hash, "text": text, "created_at": log["at"],
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        }, cache_dir)
    return result
