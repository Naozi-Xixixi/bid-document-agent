"""Multi-depot dispatch with capacity and same-day delivery windows.

A deterministic greedy heuristic, not an optimal VRP solver. All timings derive
from the provider's per-leg estimates; infeasible candidates are never hidden.
"""
from __future__ import annotations

import html
import math
from collections.abc import Awaitable, Callable
from typing import Any

Leg = dict[str, Any]
LegProvider = Callable[[list[float], list[float]], Awaitable[Leg]]
COLORS = ['#176b48', '#2453a4', '#c15a21', '#a02f77', '#7156a6', '#927000', '#007b82', '#9c4141', '#3e6385', '#5f7726']


def minute(value: str) -> int:
    hours, minutes = map(int, value.split(':'))
    if hours > 23 or minutes > 59: raise ValueError('时间格式应为 00:00–23:59')
    return hours * 60 + minutes


def time_label(minutes: float) -> str:
    n = int(math.ceil(minutes))
    return ('次日 ' if n >= 1440 else '') + f'{(n // 60) % 24:02d}:{n % 60:02d}'


def geographic_distance(a, b):
    scale = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * scale, a[1] - b[1])


async def plan(network: list[dict], demands: list[dict], vehicles: list[dict], leg_provider: LegProvider, return_to_start: bool, *, simulated: bool = False) -> dict:
    depots = {item['point_id']: item for item in network}
    if len(depots) != len(network): raise ValueError('网点编号不能重复')
    if len({x['point_id'] for x in demands}) != len(demands): raise ValueError('需求点编号不能重复')
    if len({x['vehicle_id'] for x in vehicles}) != len(vehicles): raise ValueError('车辆编号不能重复')
    for vehicle in vehicles:
        if vehicle['network_point_id'] not in depots: raise ValueError('车辆所属发车网点不存在')
        minute(vehicle['departure_time'])
    for demand in demands:
        if minute(demand['window_start']) > minute(demand['window_end']): raise ValueError('送达时间窗不能跨日，开始时间不能晚于结束时间')
    states = [{**v, 'depot': depots[v['network_point_id']], 'remaining': float(v['capacity_kg']),
               'load_kg': 0.0, 'clock': float(minute(v['departure_time'])),
               'current': depots[v['network_point_id']]['location'], 'stops': [], 'legs': []} for v in vehicles]
    cache: dict[tuple[tuple[float, ...], tuple[float, ...]], Leg] = {}
    async def get_leg(a, b):
        key = (tuple(a),tuple(b))
        if key not in cache:
            leg = await leg_provider(a,b)
            if not isinstance(leg.get('duration_seconds'), (int,float)) or not isinstance(leg.get('distance_meters'), (int,float)) or leg['duration_seconds'] < 0 or leg['distance_meters'] < 0:
                raise ValueError('道路服务未返回有效距离或时长，不能检查时间窗')
            cache[key] = leg
        return cache[key]
    assignments, unassigned = [], []
    # Earliest deadline first; deterministic ties retain user order.
    for demand in sorted(demands, key=lambda x: minute(x['window_end'])):
        weight = float(demand['weight_kg'])
        capacity_candidates = [s for s in states if s['remaining'] + 1e-8 >= weight]
        if not capacity_candidates:
            unassigned.append({'demand_point_id': demand['point_id'], 'name': demand['name'], 'reason': '各车辆剩余载重不足或未配置车辆'}); continue
        choices = []
        for state in capacity_candidates:
            leg = await get_leg(state['current'],demand['location'])
            arrival = state['clock'] + leg['duration_seconds'] / 60
            start = max(arrival, float(minute(demand['window_start'])))
            if start > minute(demand['window_end']) + 1e-8: continue
            choices.append((geographic_distance(state['depot']['location'], demand['location']), start, state['vehicle_id'], state, leg, arrival))
        if not choices:
            unassigned.append({'demand_point_id': demand['point_id'], 'name': demand['name'], 'reason': '现有车辆按当前排序无法在送达时间窗内到达'}); continue
        _, start, _, state, leg, arrival = min(choices, key=lambda x: (x[0],x[1],x[2]))
        state['remaining'] -= weight; state['load_kg'] += weight
        state['clock'] = start + float(demand['service_minutes'])
        state['current'] = demand['location']
        state['legs'].append(leg)
        stop = {**demand, 'arrival_time': time_label(arrival), 'service_time': time_label(start),
                'departure_time': time_label(state['clock']), 'wait_minutes': round(start-arrival, 1)}
        state['stops'].append(stop)
        assignments.append({'demand_point_id': demand['point_id'], 'demand_name': demand['name'], 'network_point_id': state['network_point_id'],
                            'network_point_name': state['depot']['name'], 'vehicle_id': state['vehicle_id'], 'vehicle_name': state['name'], 'arrival_time': stop['arrival_time']})
    routes = []
    for state in states:
        if not state['stops']: continue
        back = await get_leg(state['current'],state['depot']['location']) if return_to_start else None
        distance = sum(x['distance_meters'] for x in state['legs']) + (back['distance_meters'] if back else 0)
        travel = sum(x['duration_seconds'] for x in state['legs']) + (back['duration_seconds'] if back else 0)
        finish = state['clock'] + (back['duration_seconds'] / 60 if back else 0)
        routes.append({'vehicle_id': state['vehicle_id'], 'vehicle_name': state['name'], 'network_point_id': state['network_point_id'],
                       'network_point_name': state['depot']['name'], 'origin': state['depot'], 'departure_time': state['departure_time'],
                       'capacity_kg': state['capacity_kg'], 'load_kg': round(state['load_kg'],3), 'stops': state['stops'],
                       'legs': state['legs'], 'return_leg': back, 'distance_meters': int(distance),
                       'travel_seconds': int(travel), 'finish_time': time_label(finish)})
    result = {'simulated': simulated, 'provider': 'simulation' if simulated else 'amap', 'network_points': network,
              'assignments': assignments, 'routes': routes, 'unassigned': unassigned, 'return_to_start': return_to_start,
              'distance_meters': sum(r['distance_meters'] for r in routes), 'complete': not unassigned,
              'notice': '模拟数据与直线估算，仅用于测试，不能用于实际配送或投标。' if simulated else '按当前约束和高德逐段预计行驶时间进行启发式规划；不是全局最优，也不能保证实际到达时间。'}
    result['route_svg'] = route_svg(result)
    return result


def route_svg(result):
    coordinates = [p['location'] for p in result['network_points']] + [s['location'] for r in result['routes'] for s in r['stops']]
    if not coordinates: return ''
    for route in result['routes']:
        for leg in route['legs'] + ([route['return_leg']] if route['return_leg'] else []): coordinates += leg.get('polyline', [])
    xs = [p[0] for p in coordinates]; ys = [p[1] for p in coordinates]
    minx,maxx,miny,maxy = min(xs),max(xs),min(ys),max(ys)
    dx,dy = max(maxx-minx,.001),max(maxy-miny,.001)
    def point(p): return (70+(p[0]-minx)/dx*1060, 605-(p[1]-miny)/dy*450)
    def esc(s): return html.escape(str(s),quote=True)
    title = '【虚构测试】多网点配送路线示意图' if result['simulated'] else '多网点配送路线示意图（非地图底图）'
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720"><rect width="1200" height="720" fill="#f5f7f3"/>',
           f'<text x="45" y="40" font-family="Microsoft YaHei,Arial" font-size="24" font-weight="bold">{esc(title)}</text>',
           f'<text x="45" y="68" font-family="Microsoft YaHei,Arial" font-size="14">车辆 {len(result["routes"])} · 未安排 {len(result["unassigned"])} · 合计 {result["distance_meters"]/1000:.1f} 公里</text>']
    for index,route in enumerate(result['routes']):
        color = COLORS[index % len(COLORS)]
        # Separate overlapping vehicle paths slightly in this schematic.
        offset = ((index % 3) - 1) * 5
        def route_point(location):
            x, y = point(location)
            return x, y + offset
        for leg in route['legs']:
            pts = ' '.join(f'{x:.1f},{y:.1f}' for x,y in map(route_point,leg.get('polyline', [])))
            if pts: out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="5"/>')
        if route['return_leg']:
            pts = ' '.join(f'{x:.1f},{y:.1f}' for x,y in map(route_point,route['return_leg'].get('polyline', [])))
            if pts: out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="4" stroke-dasharray="10 7"/>')
        for number,stop in enumerate([route['origin'],*route['stops']]):
            x,y = route_point(stop['location']); label = ('起' if number == 0 else str(number))
            label_width = min(500, max(80, len(str(stop["name"])) * 14))
            label_x = max(20, min(1180 - label_width, x + 14))
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="{color}" stroke="white" stroke-width="2"/><text x="{x:.1f}" y="{y+4:.1f}" fill="white" font-size="12" text-anchor="middle">{label}</text><text x="{label_x:.1f}" y="{y-12:.1f}" font-family="Microsoft YaHei,Arial" font-size="13">{esc(stop["name"])}</text>')
        out.append(f'<text x="{45+(index%3)*380}" y="{95+(index//3)*22}" font-family="Microsoft YaHei,Arial" font-size="13" fill="{color}">━ {esc(route["network_point_name"])} / {esc(route["vehicle_name"])}</text>')
    out.append('<text x="45" y="690" font-family="Microsoft YaHei,Arial" font-size="13">实线：配送；虚线：返程。时长为估计值，实际发车前须复核。</text></svg>')
    return ''.join(out)
