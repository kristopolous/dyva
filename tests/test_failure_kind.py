"""_failure_kind bucketing — pytest (`uv run pytest tests/test_failure_kind.py`).

A failure's survey counter (fail_4xx/fail_5xx/fail_timeout/fail_conn/fail_other) is derived
from the verdict + the detail string. Handlers emit the HTTP status two ways: the canonical
`status {code}` (chat) and `HTTP {code}` (the decision /v1/systemone path, line 13031, plus a
couple of media handlers). Both must bucket by the status class, or a decision host's 502s pile
into fail_other and never show up as 5xx — exactly the "502s aren't being marked" report.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

FAILED = dyva.V_FAILED
TIMEOUT = dyva.V_TIMEOUT


def test_status_5xx_canonical():
    assert dyva._failure_kind(FAILED, "status 502: upstream") == "5xx"


def test_status_4xx_canonical():
    assert dyva._failure_kind(FAILED, "status 404: not found") == "4xx"


def test_http_5xx_decision_format():
    # The /v1/systemone handler emits "HTTP 502: <html>...": must still be 5xx, not other.
    detail = "HTTP 502: <html> <head><title>502 Bad Gateway</title></head> <body> ..."
    assert dyva._failure_kind(FAILED, detail) == "5xx"


def test_http_4xx_decision_format():
    assert dyva._failure_kind(FAILED, "HTTP 404: Not Found") == "4xx"


def test_http_bare_status():
    # Some handlers emit just "HTTP 503" with no colon/body.
    assert dyva._failure_kind(FAILED, "HTTP 503") == "5xx"


def test_timeout_wins_over_detail():
    assert dyva._failure_kind(TIMEOUT, "HTTP 502: ...") == "timeout"


def test_conn_detail():
    assert dyva._failure_kind(FAILED, "ClientConnectorError: cannot connect") == "conn"


def test_other_detail():
    assert dyva._failure_kind(FAILED, "bad response (not JSON)") == "other"
