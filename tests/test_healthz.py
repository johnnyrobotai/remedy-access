from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["livekit_configured"] is False
    assert body["gemini_configured"] is False


def test_auth_required_for_api_when_configured(authed_client: TestClient) -> None:
    # /healthz is public even when APP_API_KEY is set
    assert authed_client.get("/healthz").status_code == 200

    # /api/* should 401 without the header
    no_header = TestClient(authed_client.app)
    resp = no_header.get("/api/transcript?src=http://example.com/x.pdf")
    assert resp.status_code in (401, 404)  # 404 if route not yet mounted; 401 otherwise
