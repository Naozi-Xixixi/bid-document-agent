from __future__ import annotations
import asyncio
from copy import deepcopy
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.api.routes import advanced_dispatch as api
from app.services import advanced_dispatch as engine


def run(data): return engine.solve(engine.Scenario.model_validate(data))


def test_split_multitrip_cross_midnight_cold_chain_and_forbidden():
    result=run(api.demo_data())
    assert result['status']=='optimal_for_model' and result['unassigned']==[]
    assert len(result['routes'])==1 and result['routes'][0]['vehicle_id']=='冷链车A'
    route=result['routes'][0];assert route['trips']==3
    assert [e['trip'] for e in route['events']]==[1,2,3]
    assert all(e['weight_kg']==500 and e['volume_m3']==1.5 for e in route['events'])
    assert route['events'][0]['service_start']=='2026-10-30T15:50:00+00:00'
    assert route['events'][-1]['service_start']=='2026-10-30T17:30:00+00:00'  # 01:30 +08 next day
    assert route['distance_m']==60000


def test_disallow_split_volume_cold_and_number_of_trips():
    data=api.demo_data();data['orders'][0]['allow_split']=False
    assert run(data)['unassigned']==[{'order_id':'测试订单','unassigned_units':3}]
    data=api.demo_data();data['orders'][0]['unit_volume_m3']=2.1
    assert run(data)['unassigned'][0]['unassigned_units']==3
    data=api.demo_data();data['vehicles'][0]['temperature_c']=20
    assert run(data)['unassigned'][0]['unassigned_units']==3
    data=api.demo_data();data['vehicles'][0]['max_trips']=1
    result=run(data);assert result['unassigned'][0]['unassigned_units']==2 and result['routes'][0]['trips']==1


def test_simulated_congestion_and_truck_forbidden_reduce_completed_orders():
    data=api.demo_data()
    for road in data['roads']:road['duration_s']=7200
    result=run(data);assert result['unassigned']
    data=api.demo_data()
    for road in data['roads']:road['forbidden_vehicles']=['冷链车A','禁行车B','常温车C']
    result=run(data);assert result['unassigned'][0]['unassigned_units']==3 and not result['routes']


def test_time_zone_and_unknown_constraints_are_rejected():
    data=api.demo_data();data['orders'][0]['window_start']='2026-10-30T23:50:00'
    with pytest.raises(ValidationError):engine.Scenario.model_validate(data)
    data=api.demo_data();data['vehicles'][0]['unmodeled_constraint']=True
    with pytest.raises(ValidationError):engine.Scenario.model_validate(data)


def test_search_limit_reports_not_optimal_and_does_not_claim_exhaustive():
    data=api.demo_data();data['orders'][0]['units']=5;data['vehicles'][0]['capacity_kg']=3000;data['vehicles'][0]['capacity_m3']=10;data['search_limit']=100
    # Many distinct orders create distinct assignments, rather than interchangeable units.
    data['orders']=[{**data['orders'][0], 'id':f'order{i}', 'units':1} for i in range(5)]
    data['roads'][0]['forbidden_vehicles']=[];data['roads'][1]['forbidden_vehicles']=[];data['vehicles'][2]['temperature_c']=4
    result=run(data)
    assert result['status']=='best_found' and result['search_states']==100


def truck_response(restriction):
    return {'errcode':0,'data':{'route':{'paths':[{'distance':'2000','duration':'300','restriction':restriction,'steps':[{'polyline':'117.0,31.0;117.1,31.1'}]}]}}}

@pytest.mark.parametrize('restriction,usable',[(-1,True),(1,True),(0,False),(2,False),(3,False),(4,False),(5,False)])
def test_truck_restrictions_never_treat_unknown_or_forbidden_as_pass(restriction,usable):
    assert api.parse_truck(truck_response(restriction))['usable'] is usable


def test_truck_failure_never_falls_back_to_passenger_routes():
    for payload in ({'errcode':10001},{'errcode':0,'data':{'route':{'paths':[]}}},{'errcode':0,'data':{'route':{'paths':[{'distance':1}]}}}):
        with pytest.raises(HTTPException):api.parse_truck(payload)


def test_truck_adapter_sends_vehicle_specific_parameters():
    seen=[]
    class Resp:
        def raise_for_status(self):pass
        def json(self):return truck_response(1)
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,*,params):seen.append((url,params));return Resp()
    payload=api.TruckLeg(origin=[117.1,31.8],destination=[117.2,31.9],size=2,height_m=2.5,width_m=2,total_weight_t=4,rated_load_t=1.5,axles=2,province='皖',plate_number='A12345',cartype=1)
    with patch.object(api.settings,'amap_web_service_key','test-key'),patch.object(api.httpx,'AsyncClient',Client):
        result=asyncio.run(api.truck_leg(payload))
    assert result['usable'] and seen[0][0].endswith('/v4/direction/truck')
    p=seen[0][1]; assert (p['size'],p['height'],p['width'],p['load'],p['weight'],p['axis'],p['province'],p['number'],p['cartype'])==(2,2.5,2,4,1.5,2,'皖','A12345',1)


def test_bounded_exact_search_can_find_lower_distance_assignment():
    data=api.demo_data();data['vehicles']=data['vehicles'][:2]
    for v in data['vehicles']:v['max_trips']=1;v['capacity_kg']=1000;v['capacity_m3']=10
    data['vehicles'][1]['temperature_c']=4
    data['nodes']=['虚构配送中心','虚构学校','近点']
    data['orders']=[{**data['orders'][0],'id':'o1','units':1},{**data['orders'][0],'id':'o2','destination':'近点','units':1}]
    data['roads']=[{'origin':a,'destination':b,'distance_m':d,'duration_s':60,'forbidden_vehicles':[]} for a,b,d in [('虚构配送中心','虚构学校',10),('虚构学校','虚构配送中心',10),('虚构配送中心','近点',10),('近点','虚构配送中心',10),('虚构学校','近点',1),('近点','虚构学校',1)]]
    result=run(data)
    assert result['status']=='optimal_for_model' and not result['unassigned'] and result['total_distance_m']==21


def test_replan_preserves_completed_units_and_spent_trips():
    data=api.demo_data()
    state=api.ReplanState(scenario=data,now='2026-10-31T00:20:00+08:00',completed_units={'测试订单':1},completed_trips={'冷链车A':1},vehicle_positions={v['id']:v['depot'] for v in data['vehicles']})
    result=asyncio.run(api.simulate_replan(state))
    assert result['completed_units']=={'测试订单':1} and result['unassigned']==[]
    assert sum(e['units'] for r in result['routes'] for e in r['events'])==2
    assert result['routes'][0]['vehicle_id']=='冷链车A' and result['routes'][0]['trips']==2
    assert result['routes'][0]['events'][0]['arrival'] > '2026-10-30T16:20:00+00:00'


def test_replan_refuses_in_transit_and_invalid_completed_amounts():
    data=api.demo_data();state=api.ReplanState(scenario=data,now='2026-10-31T00:20:00+08:00',completed_units={'测试订单':1},completed_trips={'冷链车A':1},vehicle_positions={v['id']:v['depot'] for v in data['vehicles']})
    state.vehicle_positions['冷链车A']='虚构学校'
    with pytest.raises(HTTPException) as exc:asyncio.run(api.simulate_replan(state))
    assert exc.value.status_code==422
    state.vehicle_positions['冷链车A']='虚构配送中心';state.completed_units['测试订单']=4
    with pytest.raises(HTTPException):asyncio.run(api.simulate_replan(state))


def test_http_demo_requires_login_and_produces_fictional_data():
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.main import app
    from app.services import auth_service
    tenant=SimpleNamespace(company_id='test',company_name='演示')
    with patch.object(auth_service,'authenticate_token',side_effect=lambda token: ({},{}) if token=='valid' else None), patch.object(auth_service,'build_tenant_context',return_value=tenant):
        client=TestClient(app)
        assert client.get('/api/maps/advanced-dispatch/demo').status_code==401
        response=client.get('/api/maps/advanced-dispatch/demo',headers={'Authorization':'Bearer valid'})
        assert response.status_code==200 and response.json()['result']['simulation'] is True
        response=client.post('/api/maps/advanced-dispatch/simulate',json=api.demo_data(),headers={'Authorization':'Bearer valid'})
        assert response.status_code==200 and response.json()['status']=='optimal_for_model'
        client.close()


def test_real_truck_matrix_uses_vehicle_specific_costs_and_restrictions():
    seen=[]
    class Resp:
        def __init__(self,params):self.params=params
        def raise_for_status(self):pass
        def json(self):return truck_response(0 if self.params['number']=='A11111' else 1)
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,*,params):seen.append(params);return Resp(params)
    scenario=api.demo_data();scenario['vehicles']=scenario['vehicles'][:2];scenario['roads']=[]
    profile={'size':2,'height_m':2.5,'width_m':2,'total_weight_t':4,'rated_load_t':0.8,'axles':2,'province':'皖','cartype':0}
    payload=api.RealTruckPlan(scenario=scenario,node_locations={'虚构配送中心':[117.1,31.8],'虚构学校':[117.2,31.9]},trucks={'冷链车A':{**profile,'plate_number':'A11111'},'禁行车B':{**profile,'plate_number':'B22222'}})
    with patch.object(api.settings,'amap_web_service_key','test-key'),patch.object(api.httpx,'AsyncClient',Client),patch.object(api._PacedAmapClient,'interval_seconds',0):
        result=asyncio.run(api.truck_plan(payload))
    assert not result['simulation'] and result['provider']=='amap_truck_current_snapshot'
    assert len(seen)==4 and len(result['road_checks'])==4
    assert all(not x['usable'] for x in result['road_checks'] if x['vehicle_id']=='冷链车A')
    assert not result['unassigned'] and result['routes'][0]['vehicle_id']=='禁行车B' and result['routes'][0]['trips']==3


def test_real_matrix_rejects_untrusted_input_routes_and_vehicle_overload():
    scenario=api.demo_data();scenario['vehicles']=scenario['vehicles'][:1]
    for road in scenario['roads']:road['forbidden_vehicles']=[]
    profile={'size':2,'height_m':2.5,'width_m':2,'total_weight_t':4,'rated_load_t':0.8,'axles':2,'province':'皖','cartype':0,'plate_number':'A11111'}
    request=api.RealTruckPlan(scenario=scenario,node_locations={'虚构配送中心':[117.1,31.8],'虚构学校':[117.2,31.9]},trucks={'冷链车A':profile})
    with pytest.raises(HTTPException):asyncio.run(api.truck_plan(request))
    request.scenario.roads=[];request.trucks['冷链车A'].rated_load_t=0.1
    with pytest.raises(HTTPException):asyncio.run(api.truck_plan(request))
