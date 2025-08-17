def test_ratelimiting(client):
    # Test that the rate limiter is working
    for _ in range(10):
        response = client.get("/healthz")
        assert response.status_code == 200  # noqa: PLR2004

    response = client.get("/healthz")
    assert response.status_code == 429  # noqa: PLR2004
