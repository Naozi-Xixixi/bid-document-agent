from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from app.services import multi_depot_route as planner
from app.api.routes import multi_depot
from app.core.tenant_context import TenantContext, set_current_tenant, reset_current_tenant
from app.main import app
from app.services import auth_service
from fastapi.testclient import TestClient


def net(id,name,x): return {'point_id':id,'name':name,'address':name,'location':[x,0.0]}
def demand(id,x,weight,start='08:00',end='10:00',service=10): return {'point_id':id,'name':id,'address':id,'location':[x,0.0],'weight_kg':weight,'window_start':start,'window_end':end,'service_minutes':service}
def vehicle(id,point,capacity=1000,departure='07:00'): return {'vehicle_id':id,'name':id,'network_point_id':point,'capacity_kg':capacity,'departure_time':departure}
async def leg(a,b): return {'distance_meters':1000,'duration_seconds':600,'polyline':[a,b]}


def test_multiple_depots_vehicles_capacity_windows_wait_and_return():
    network=[net('a','网点 A',0),net('b','网点 B',10)]
    demands=[demand('a1',1,600),demand('a2',2,500),demand('b1',9,600),demand('too-heavy',5,2000)]
    vehicles=[vehicle('a-car-1','a',700),vehicle('a-car-2','a',700),vehicle('b-car','b',700)]
    result=asyncio.run(planner.plan(network,demands,vehicles,leg,True))
    assert len(result['routes']) == 3
    assert {x['demand_point_id']: x['network_point_id'] for x in result['assignments']} == {'a1':'a','a2':'a','b1':'b'}
    assert len(result['unassigned'])==1 and result['unassigned'][0]['demand_point_id']=='too-heavy'
    assert all(r['load_kg'] <= r['capacity_kg'] and r['stops'][0]['arrival_time']=='07:10' and r['stops'][0]['service_time']=='08:00' for r in result['routes'])
    assert all(r['return_leg'] is not None and r['finish_time']=='08:20' for r in result['routes'])
    assert result['distance_meters']==6000 and not result['complete']


def test_failed_time_window_not_claimed_completed():
    r=asyncio.run(planner.plan([net('a','网点',0)],[demand('late',1,100,'07:00','07:05')],[vehicle('v','a')],leg,False))
    assert r['unassigned'][0]['demand_point_id']=='late'
    assert not r['complete'] and r['routes']==[]


def test_alternate_depot_when_nearest_lacks_capacity_and_no_return():
    r=asyncio.run(planner.plan([net('a','近网点',0),net('b','远网点',10)],[demand('point',1,800)],[vehicle('small','a',100),vehicle('large','b',1000)],leg,False))
    assert r['assignments'][0]['network_point_id']=='b'
    assert r['routes'][0]['return_leg'] is None and r['routes'][0]['distance_meters']==1000


def test_demo_is_virtual_multi_depot_and_does_not_call_network():
    with patch.object(multi_depot.httpx,'AsyncClient',side_effect=AssertionError('demo must stay offline')):
        result=asyncio.run(multi_depot.multi_depot_demo())
    assert result['simulated'] and result['provider']=='simulation'
    assert len(result['routes'])==3 and len({r['network_point_id'] for r in result['routes']})==2
    assert len(result['unassigned'])==1 and '虚构' in result['route_svg'] and '不能用于' in result['notice']


def test_invalid_time_duplicate_and_missing_vehicle_depot_rejected_before_network():
    payload=multi_depot.RequestPlan(city='示例市',network_points=[{'point_id':'a','name':'网点','address':'网点地址'}],
        demand_points=[{'point_id':'d','name':'学校','address':'学校地址','weight_kg':200,'window_start':'08:00','window_end':'07:00'}],
        vehicles=[{'vehicle_id':'v','name':'车','network_point_id':'a','capacity_kg':500,'departure_time':'07:00'}])
    with patch.object(multi_depot.httpx,'AsyncClient',side_effect=AssertionError('must fail before networking')):
        with pytest.raises(HTTPException) as exc: asyncio.run(multi_depot.multi_depot_route_api(payload))
    assert exc.value.status_code==422
    payload.demand_points[0].window_end='09:00';payload.vehicles[0].network_point_id='missing'
    with pytest.raises(HTTPException): multi_depot.validate(payload)


def test_real_api_uses_road_leg_times_for_every_origin_and_return():
    requests=[]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,*,params):
            requests.append(params)
            class Response:
                def raise_for_status(self): pass
                def json(self): return {'status':'1','route':{'paths':[{'distance':'1000','duration':'600','steps':[{'polyline':params['origin']+';'+params['destination']}]}]}}
            return Response()
    async def geocode(client, stop, city, key):
        coords={'网点甲':[1.,1.], '网点乙':[10.,1.], '学校甲':[2.,1.], '学校乙':[9.,1.]}
        return {'name':stop.name,'address':stop.address,'formatted_address':stop.address,'location':coords[stop.name]}
    payload=multi_depot.RequestPlan(city='示例市',network_points=[{'point_id':'a','name':'网点甲','address':'网点甲地址'},{'point_id':'b','name':'网点乙','address':'网点乙地址'}],
        demand_points=[{'point_id':'d1','name':'学校甲','address':'学校甲地址','weight_kg':100,'window_start':'07:00','window_end':'08:00'}, {'point_id':'d2','name':'学校乙','address':'学校乙地址','weight_kg':100,'window_start':'07:00','window_end':'08:00'}],
        vehicles=[{'vehicle_id':'v1','name':'车甲','network_point_id':'a','capacity_kg':200,'departure_time':'07:00'},{'vehicle_id':'v2','name':'车乙','network_point_id':'b','capacity_kg':200,'departure_time':'07:00'}])
    with patch.object(multi_depot.settings,'amap_web_service_key','test-key'), patch.object(multi_depot.maps,'_geocode',geocode), patch.object(multi_depot.httpx,'AsyncClient',Client), patch.object(multi_depot._PacedAmapClient,'interval_seconds',0):
        result=asyncio.run(multi_depot.multi_depot_route_api(payload))
    assert result['complete'] and len(result['routes'])==2
    assert {x['network_point_id'] for x in result['routes']}=={'a','b'}
    assert all(r['stops'][0]['service_time']=='07:10' and r['finish_time']=='07:20' for r in result['routes'])
    assert any(p['origin']=='2.0,1.0' and p['destination']=='1.0,1.0' for p in requests)
    assert any(p['origin']=='9.0,1.0' and p['destination']=='10.0,1.0' for p in requests)
    assert all(r['return_leg'] is not None for r in result['routes'])


def test_missing_provider_geometry_or_time_is_error():
    with pytest.raises(HTTPException): multi_depot.parse_route({'status':'1','route':{'paths':[{'distance':'1000','steps':[]}]}})


def test_saved_routes_are_company_isolated_and_demo_rejected():
    base=Path(__file__).resolve().parents[2]/'.cache'/'multi-route-tests';base.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as folder, patch.object(multi_depot.settings,'data_dir',Path(folder)), patch.object(multi_depot.settings,'database_path',None), patch.object(multi_depot.settings,'db_backend','sqlite'):
        root=Path(folder)
        tenant_a=TenantContext(user_id=1,username='a',role='company_user',company_id='a',company_name='甲',database_file=root/'a'/'db.sqlite',upload_dir=root/'a'/'uploads')
        tenant_b=TenantContext(user_id=2,username='b',role='company_user',company_id='b',company_name='乙',database_file=root/'b'/'db.sqlite',upload_dir=root/'b'/'uploads')
        inputs=multi_depot.RequestPlan(network_points=[{'point_id':'n','name':'网点','address':'网点地址'}], demand_points=[{'point_id':'d','name':'学校','address':'学校地址','weight_kg':100,'window_start':'07:00','window_end':'09:00'}],vehicles=[{'vehicle_id':'v','name':'车','network_point_id':'n','capacity_kg':500,'departure_time':'06:00'}])
        token=set_current_tenant(tenant_a)
        try:
            with pytest.raises(HTTPException): asyncio.run(multi_depot.save_plan(multi_depot.SavePlan(title='虚构',inputs=inputs,result={'simulated':True,'provider':'simulation'})))
            saved=asyncio.run(multi_depot.save_plan(multi_depot.SavePlan(title='正式方案',inputs=inputs,result={'simulated':False,'provider':'amap','routes':[]})))
            assert asyncio.run(multi_depot.get_plan(saved['id']))['created_by']=='a'
            assert len(asyncio.run(multi_depot.list_plans())['plans'])==1
        finally: reset_current_tenant(token)
        token=set_current_tenant(tenant_b)
        try:
            assert asyncio.run(multi_depot.list_plans())['plans']==[]
            with pytest.raises(HTTPException) as exc: asyncio.run(multi_depot.get_plan(saved['id']))
            assert exc.value.status_code==404
        finally: reset_current_tenant(token)


def test_demo_http_requires_auth():
    tenant=SimpleNamespace(company_id='demo',company_name='演示')
    with patch.object(auth_service,'authenticate_token',side_effect=lambda token: ({},{}) if token=='valid' else None), patch.object(auth_service,'build_tenant_context',return_value=tenant):
        client=TestClient(app)
        assert client.get('/api/maps/multi-depot-route/demo').status_code == 401
        response=client.get('/api/maps/multi-depot-route/demo',headers={'Authorization':'Bearer valid'})
        assert response.status_code == 200 and response.json()['simulated'] is True
        client.close()


def test_rate_limit_is_retried_and_final_failure_is_explicit():
    class Resp:
        def __init__(self,info): self.info=info
        def json(self): return {'info':self.info}
    class Client:
        def __init__(self,answers): self.answers=iter(answers);self.calls=0
        async def get(self,url,*,params): self.calls+=1;return Resp(next(self.answers))
    client=Client(['CUQPS_HAS_EXCEEDED_THE_LIMIT','OK'])
    with patch.object(multi_depot._PacedAmapClient,'interval_seconds',0):
        assert asyncio.run(multi_depot._PacedAmapClient(client).get('x',params={})).json()['info']=='OK'
    assert client.calls==2
    client=Client(['CUQPS_HAS_EXCEEDED_THE_LIMIT']*3)
    with patch.object(multi_depot._PacedAmapClient,'interval_seconds',0), pytest.raises(HTTPException) as exc:
        asyncio.run(multi_depot._PacedAmapClient(client).get('x',params={}))
    assert exc.value.status_code==429 and client.calls==3


def test_unsupported_constraints_are_rejected_instead_of_silently_ignored():
    from pydantic import ValidationError
    vehicle = {'vehicle_id':'v','name':'车','network_point_id':'n','capacity_kg':500,'departure_time':'07:00'}
    for extra in ({'volume_m3':10}, {'refrigerated': True}, {'max_trips': 2}, {'vehicle_type': '货车'}):
        with pytest.raises(ValidationError): multi_depot.Vehicle(**{**vehicle, **extra})
    demand = {'point_id':'d','name':'学校','address':'学校地址','weight_kg':100,'window_start':'07:00','window_end':'09:00'}
    with pytest.raises(ValidationError): multi_depot.Demand(**{**demand,'volume_m3':1})


def test_geocoder_house_number_mismatch_or_missing_is_rejected():
    source=multi_depot.maps.RouteStop(name='地点',address='示例市示例路1555号')
    for returned in ['华东省示例市示例路39号','华东省示例市示例路']:
        with pytest.raises(HTTPException) as exc:
            multi_depot.verify_geocoded_address(source, {'formatted_address':returned})
        assert exc.value.status_code==422 and '门牌号不一致' in exc.value.detail
    multi_depot.verify_geocoded_address(source, {'formatted_address':'华东省示例市示例路1555号'})
    with pytest.raises(HTTPException):
        multi_depot.verify_geocoded_address(source, {'formatted_address':source.address,'geocode_formatted_address':''})
    source=multi_depot.maps.RouteStop(name='地点',address='示例路1号')
    multi_depot.verify_geocoded_address(source, {'formatted_address':'示例市示例路1号'})
