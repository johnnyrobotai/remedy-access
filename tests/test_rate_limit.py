from fastapi.testclient import TestClient


def test_api_rate_limit_returns_429(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_API_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")

    from backend.app.config import get_settings
    from backend.app.main import _rate_limit_buckets, app

    get_settings.cache_clear()
    _rate_limit_buckets.clear()

    with TestClient(app) as client:
        first = client.get(
            "/api/transcript?src=https://example.com/sample.pdf",
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        second = client.get(
            "/api/transcript?src=https://example.com/sample.pdf",
            headers={"X-Forwarded-For": "203.0.113.10"},
        )

    assert first.status_code == 404
    assert second.status_code == 429
    assert second.json()["error"] == "rate_limited"
    assert int(second.headers["Retry-After"]) > 0
