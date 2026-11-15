"""외부 호출 실패 구분(PRD FR-52, AT-23·24·25·29·30). 실제 네트워크는 쓰지 않는다."""
import pytest
import requests

from f0.apiclient import ApiCallError, PlatformClient

KEY = "secret-test-key-123"
CFG = {"key_header": "api_user_key_id", "retries": 2, "retry_backoff_sec": 0, "timeout_sec": 1,
       "api": {"url": "https://example.test/api", "params": {"x": "{x}"}, "total_count_field": "totalCount"}}
OK_XML = ("<response><header><resultCode>00</resultCode></header><body><items><item><a>1</a></item></items>"
          "<totalCount>1</totalCount></body></response>")


class Resp:
    def __init__(self, status=200, body=OK_XML, ctype="application/xml", headers=None):
        self.status_code, self.content = status, body.encode("utf-8")
        self.text = body
        self.headers = {"Content-Type": ctype, **(headers or {})}


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.max_redirects = 30

    def get(self, url, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses):
    c = PlatformClient(api_key=KEY, config=CFG)
    c.session = Session(*responses)
    return c


def test_ok_and_redirects_off():
    c = client(Resp())
    items, total = c.call("api", x=1)
    assert items == [{"a": "1"}] and total == 1
    assert c.session.calls[0]["allow_redirects"] is False


@pytest.mark.parametrize("status, kind", [(401, "auth"), (403, "forbidden")])
def test_auth_errors_are_not_retried(status, kind):
    c = client(Resp(status), Resp())
    with pytest.raises(ApiCallError) as e:
        c.call("api", x=1)
    assert e.value.kind == kind and len(c.session.calls) == 1


def test_redirect_is_not_followed():
    c = client(Resp(302, headers={"Location": "https://other.test/"}))
    with pytest.raises(ApiCallError) as e:
        c.call("api", x=1)
    assert e.value.kind == "redirect" and len(c.session.calls) == 1


def test_429_retries_then_fails_with_kind():
    c = client(Resp(429), Resp(429), Resp(429))
    with pytest.raises(ApiCallError) as e:
        c.call("api", x=1)
    assert e.value.kind == "rate_limited" and len(c.session.calls) == 3


def test_server_error_then_success():
    c = client(Resp(503), Resp())
    assert c.call("api", x=1)[1] == 1


def test_timeout_kind():
    c = client(requests.Timeout(), requests.Timeout(), requests.Timeout())
    with pytest.raises(ApiCallError) as e:
        c.call("api", x=1)
    assert e.value.kind == "timeout"


@pytest.mark.parametrize("body, ctype", [
    ("<response><header><resultCode>30</resultCode><resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg>"
     "</header></response>", "application/xml"),
    ('{"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}}', "application/json"),
    ('{"error": [{"code": "-98", "message": "검색 결과가 없습니다"}]}', "application/json"),
])
def test_http_200_with_provider_error_body_fails(body, ctype):
    with pytest.raises(ApiCallError) as e:
        client(Resp(200, body, ctype)).call("api", x=1)
    assert e.value.kind == "provider_error"


@pytest.mark.parametrize("body", ["", "not xml or json", "<response><broken"])
def test_unreadable_body_is_invalid_response(body):
    with pytest.raises(ApiCallError) as e:
        client(Resp(200, body, "text/plain")).call("api", x=1)
    assert e.value.kind == "invalid_response"


def test_error_messages_never_contain_key():
    for r in (Resp(401), Resp(302), Resp(400), Resp(200, "<x><resultCode>99</resultCode></x>")):
        with pytest.raises(ApiCallError) as e:
            client(r).call("api", x=1)
        assert KEY not in str(e.value)
