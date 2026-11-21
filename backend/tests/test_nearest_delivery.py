import asyncio
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from app.api.routes import maps, multi_depot

class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data

def make_payload(count=20):
    return maps.DeliveryRouteRequest(city='示例市', network_points=[{'point_id':str(i),'name':f'虚拟网点{i}','address':f'地址{i}'} for i in range(count)], demand_points=[{'point_id':'d','name':'需求点','address':'需求地址'}], auto_nearest=True, return_to_start=False)

@pytest.mark.parametrize('fail', [False, True])
def test_select_by_road_distance_and_reject_partial_comparison(fail):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, *, params):
            n=int(float(params['origin'].split(',')[0]))
            if fail and n==5: return Response({'status':'0','info':'failed'})
            return Response({'status':'1','route':{'paths':[{'distance':str(abs(n-13)*100+1),'duration':'61','steps':[{'polyline':params['origin']+';'+params['destination']}]}]}})
    async def geocode(client, stop, city, key):
        n=int(stop.point_id) if stop.point_id!='d' else 100
        return {'name':stop.name,'address':stop.address,'formatted_address':stop.address,'location':[float(n),1.0]}
    with patch.object(maps.settings,'amap_web_service_key','test-key'), patch.object(maps,'_geocode',geocode), patch.object(maps.httpx,'AsyncClient',Client), patch.object(multi_depot._PacedAmapClient,'interval_seconds',0):
        if fail:
            with pytest.raises(HTTPException) as exc: asyncio.run(maps.plan_delivery_route(make_payload()))
            assert exc.value.detail['code']=='NEAREST_COMPARISON_FAILED'
        else:
            result=asyncio.run(maps.plan_delivery_route(make_payload()))
            assert result['start_point_id']=='13' and len(result['nearest_comparison'])==20
            assert result['stops'][0]['point_id']=='13' and result['assignments'][0]['network_point_id']=='13'
            assert result['duration_seconds']==61

def test_auto_nearest_rejects_multiple_demands():
    payload=make_payload(2)
    payload.demand_points.append(maps.DeliveryDemandPoint(point_id='d2',name='需求2',address='地址2'))
    with patch.object(maps.settings,'amap_web_service_key','test-key'), pytest.raises(HTTPException) as exc:
        asyncio.run(maps.plan_delivery_route(payload))
    assert exc.value.detail['code']=='AUTO_NEAREST_REQUIRES_ONE_DEMAND'
