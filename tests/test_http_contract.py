async def test_rejected_preflight_uses_correlated_json_error(client, assert_error):
    response = await client.options(
        "/api/v1/devices",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert_error(response, 400)
    assert "Access-Control-Allow-Origin" not in response.headers


async def test_preflight_cannot_bypass_host_allowlist(client, assert_error):
    response = await client.options(
        "/api/v1/devices",
        headers={
            "Host": "untrusted.example",
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert_error(response, 400)
    assert response.json()["error"]["code"] == "invalid_host"
