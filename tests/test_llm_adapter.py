"""LLM 어댑터 테스트 — 실제 호출 없이 requests.post 를 가짜로 바꿔 검사한다."""
import json

import pytest
import requests

from adapters import llm

KEY = "sk-test-SECRET-VALUE"


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


def ok_body(text="{}"):
    return {"model": "m1", "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


@pytest.fixture
def cfg():
    return llm.LLMConfig(base_url="https://gw.example", api_key=KEY, model="m1", max_retries=2)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_success_writes_cache_and_log_without_secret(monkeypatch, tmp_path, cfg):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json))
        return FakeResp(200, ok_body('{"a": 1}'))

    monkeypatch.setattr(requests, "post", fake_post)
    log = tmp_path / "log.jsonl"
    r = llm.complete("sys", "원문 텍스트", prompt_version="v1", config=cfg,
                     cache_dir=tmp_path / "c", log_path=log)
    assert r.text == '{"a": 1}' and r.source == "live"
    url, headers, body = calls[0]
    assert url == "https://gw.example/v1/messages"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "m1" and body["system"] == "sys"

    log_text = log.read_text(encoding="utf-8")
    cache_text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "c").iterdir())
    for t in (log_text, cache_text):
        assert KEY not in t
        assert "원문 텍스트" not in t  # 원문은 해시로만 남긴다
    assert json.loads(log_text)["input_hash"] == llm.sha256("원문 텍스트")


def test_cache_hit_skips_call(monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(200, ok_body("X")))
    llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)

    def boom(*a, **k):
        raise AssertionError("호출하면 안 됨")

    monkeypatch.setattr(requests, "post", boom)
    r = llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)
    assert r.source == "cache" and r.text == "X"


def test_cache_usable_without_key(monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(200, ok_body("X")))
    llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)
    monkeypatch.setattr(llm, "load_config", lambda *a, **k: None)
    r = llm.complete("s", "u", prompt_version="v1", model="m1", cache_dir=tmp_path, log_path=None)
    assert r.source == "cache"


def test_prompt_version_changes_cache_key(cfg):
    assert llm.cache_key("m", "v1", "s", "u") != llm.cache_key("m", "v2", "s", "u")
    assert llm.cache_key("m", "v1", "s", "u") != llm.cache_key("m2", "v1", "s", "u")


def test_retry_then_success(monkeypatch, tmp_path, cfg):
    seq = [FakeResp(503, {}), FakeResp(429, {}), FakeResp(200, ok_body("ok"))]
    monkeypatch.setattr(requests, "post", lambda *a, **k: seq.pop(0))
    r = llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)
    assert r.text == "ok"


def test_retry_exhausted_raises(monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(503, {}))
    with pytest.raises(llm.LLMError, match="503"):
        llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)
    assert not any(tmp_path.iterdir())  # 실패는 저장하지 않는다


def test_auth_error_message_has_no_secret(monkeypatch, tmp_path, cfg):
    body = {"error": {"type": "authentication_error", "message": f"bad key {KEY} for 원문"}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(401, body))
    with pytest.raises(llm.LLMError) as e:
        llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)
    assert KEY not in str(e.value) and "원문" not in str(e.value)
    assert "401" in str(e.value)


def test_empty_response_is_error(monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(200, ok_body("  ")))
    with pytest.raises(llm.LLMError):
        llm.complete("s", "u", prompt_version="v1", config=cfg, cache_dir=tmp_path, log_path=None)


def test_no_config_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "load_config", lambda *a, **k: None)
    with pytest.raises(llm.LLMError, match="설정 없음"):
        llm.complete("s", "u", prompt_version="v1", cache_dir=tmp_path, log_path=None)


def test_config_repr_hides_key(cfg):
    assert KEY not in repr(cfg)
