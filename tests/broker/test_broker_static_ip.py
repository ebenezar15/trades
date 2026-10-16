import httpx

from indexedge.broker.static_ip import check_static_ip, public_ip


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_public_ip_plain_and_json():
    assert await public_ip("https://ip.test", client=_client(lambda r: httpx.Response(200, text="1.2.3.4\n"))) \
        == "1.2.3.4"
    js = _client(lambda r: httpx.Response(200, json={"ip": "5.6.7.8"}))
    assert await public_ip("https://ip.test", client=js) == "5.6.7.8"


async def test_public_ip_errors_return_none():
    assert await public_ip("https://ip.test", client=_client(lambda r: httpx.Response(503))) is None
    assert await public_ip("https://ip.test", client=_client(lambda r: httpx.Response(200, text="<html>"))) is None

    def boom(r):
        raise httpx.ConnectError("offline")

    assert await public_ip("https://ip.test", client=_client(boom)) is None


async def test_check_static_ip_cases():
    ok = _client(lambda r: httpx.Response(200, text="1.2.3.4"))
    res = await check_static_ip("1.2.3.4", "https://ip.test", client=ok)
    assert res == {"ok": True, "public_ip": "1.2.3.4", "expected": "1.2.3.4",
                   "message": "Public IP matches the registered static IP."}
    res = await check_static_ip("9.9.9.9", "https://ip.test", client=ok)
    assert not res["ok"] and "does not match" in res["message"]
    res = await check_static_ip("", "https://ip.test", client=ok)
    assert not res["ok"] and "No static IP" in res["message"]
    down = _client(lambda r: httpx.Response(500))
    res = await check_static_ip("1.2.3.4", "https://ip.test", client=down)
    assert not res["ok"] and res["public_ip"] is None
    v6 = _client(lambda r: httpx.Response(200, text="2001:db8::1"))
    assert "IPv6" in (await check_static_ip("1.2.3.4", "https://ip.test", client=v6))["message"]
