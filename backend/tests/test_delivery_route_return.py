import asyncio
from unittest.mock import patch

import pytest

from app.api.routes import maps


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.mark.parametrize("return_to_start", [True, False])
def test_delivery_and_return_are_distinct_and_totalled(return_to_start):
    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, *, params):
            requests.append(dict(params))
            back = len(requests) == 2
            return Response({"status": "1", "route": {"paths": [{
                "distance": "3000" if back else "5000", "duration": "600" if back else "900",
                "steps": [{"polyline": "2,2;1,1" if back else "1,1;2,2"}],
            }]}})

    async def geocode(client, stop, city, key):
        return {"name": stop.name, "address": stop.address, "formatted_address": stop.address,
                "location": [1.0, 1.0] if stop.name == "起点" else [2.0, 2.0]}

    payload = maps.DeliveryRouteRequest(
        city="示例市", network_points=[{"point_id": "n1", "name": "起点", "address": "起点地址"}],
        demand_points=[{"point_id": "d1", "name": "学校", "address": "学校地址"}],
        return_to_start=return_to_start,
    )
    with patch.object(maps.settings, "amap_web_service_key", "test-key"), patch.object(maps, "_geocode", geocode), patch.object(maps.httpx, "AsyncClient", Client):
        result = asyncio.run(maps.plan_delivery_route(payload))

    assert result["outbound_polyline"] == [[1.0, 1.0], [2.0, 2.0]]
    assert result["return_polyline"] == ([[2.0, 2.0], [1.0, 1.0]] if return_to_start else [])
    assert result["distance_meters"] == (8000 if return_to_start else 5000)
    assert result["duration_seconds"] == (1500 if return_to_start else 900)
    assert len(requests) == (2 if return_to_start else 1)
    if return_to_start:
        assert requests[1]["origin"] == requests[0]["destination"]
        assert requests[1]["destination"] == requests[0]["origin"]
        assert 'stroke-dasharray="12 9"' in result["route_svg"]


@pytest.mark.parametrize("zoom, expected", [(11.5, "11"), (20, "17"), (3, "3")])
def test_static_map_uses_supported_integer_zoom(zoom, expected):
    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, *, params):
            requests.append(params)
            response = Response({})
            response.headers = {"content-type": "image/png"}
            response.content = b"png-test"
            return response

    payload = maps.DeliveryStaticMapRequest(center=[117.2, 31.85], locations=[[117.2, 31.85]], zoom=zoom)
    with patch.object(maps.settings, "amap_web_service_key", "test-key"), patch.object(maps.httpx, "AsyncClient", Client):
        result = asyncio.run(maps.delivery_route_static_map(payload))
    assert requests[0]["zoom"] == expected
    assert result.body == b"png-test"
