import re

with open("main.py", "r") as f:
    content = f.read()

match = re.search(r'(@app\.get\("/api/journey"\)\nasync def get_journey\(.*?\):\n.*?)(?=\n# Serve static files|\n@app)', content, re.DOTALL)
if not match:
    print("Could not find get_journey")
    exit(1)

old_func = match.group(1)

new_func = """@app.get("/api/journey")
async def get_journey(orig_lat: float, orig_lng: float, dest_lat: float, dest_lng: float):
    if not STOPS_CACHE or not TRANSIT_GRAPH:
        return {"success": False, "error": "Graph not initialized"}
        
    import heapq
    WALK_SPEED = 66.66 # meters per min
    
    orig_stops = []
    for sid, sdata in STOPS_CACHE.items():
        d = calculate_haversine(orig_lat, orig_lng, sdata['lat'], sdata['lng']) * 1.4
        if d <= 600 * 1.4:
            orig_stops.append((sid, d / WALK_SPEED))
            
    dest_stops = {}
    for sid, sdata in STOPS_CACHE.items():
        d = calculate_haversine(dest_lat, dest_lng, sdata['lat'], sdata['lng']) * 1.4
        if d <= 600 * 1.4:
            dest_stops[sid] = d / WALK_SPEED
            
    disabled_lines = set()
    best_overall_route = None
    
    for retry in range(4):
        pq = []
        for sid, w in orig_stops:
            heapq.heappush(pq, (w, sid, [(sid, w, 'walk', 'walk', 'walk', None)]))
            
        visited = {}
        best_path = None
        best_weight = float('inf')
        
        while pq:
            curr_w, u, path = heapq.heappop(pq)
            if curr_w >= best_weight: continue
            if u in visited and visited[u] <= curr_w: continue
            visited[u] = curr_w
            
            if u in dest_stops:
                final_w = curr_w + dest_stops[u]
                if final_w < best_weight:
                    best_weight = final_w
                    best_path = path + [('DEST', dest_stops[u], 'walk', 'walk', 'walk', None)]
                    
            for v, w_edge, edge_type, ref, ttype, rid in TRANSIT_GRAPH.get(u, []):
                if edge_type == 'transit' and (ref, ttype) in disabled_lines:
                    continue
                
                new_w = curr_w + w_edge
                if edge_type == 'transit' and path:
                    prev_edge_type = path[-1][2]
                    prev_ref = path[-1][3]
                    if prev_edge_type == 'transit' and prev_ref != ref:
                        new_w += 5.0 # 5 min penalty
                        
                if new_w < best_weight:
                    heapq.heappush(pq, (new_w, v, path + [(v, w_edge, edge_type, ref, ttype, rid)]))
                    
        if not best_path:
            break
            
        legs = []
        first_stop = best_path[0][0]
        legs.append({
            'type': 'walk',
            'time': round(best_path[0][1]),
            'orig_lat': orig_lat, 'orig_lng': orig_lng,
            'dest_stop': STOPS_CACHE[first_stop]['name']
        })
        current_leg = None
        route_ids_used = set()
        
        for i in range(1, len(best_path)):
            node, w_edge, edge_type, ref, ttype, rid = best_path[i]
            prev_node = best_path[i-1][0]
            
            if node == 'DEST':
                if current_leg: legs.append(current_leg)
                legs.append({
                    'type': 'walk',
                    'time': round(w_edge),
                    'orig_stop': STOPS_CACHE[prev_node]['name'],
                    'dest_lat': dest_lat, 'dest_lng': dest_lng
                })
                break
                
            if edge_type == 'walk' or edge_type == 'transfer':
                if current_leg: 
                    legs.append(current_leg)
                    current_leg = None
                legs.append({
                    'type': 'walk',
                    'time': round(w_edge),
                    'orig_stop': STOPS_CACHE[prev_node]['name'],
                    'dest_stop': STOPS_CACHE[node]['name']
                })
            else:
                if rid: route_ids_used.add(rid)
                if not current_leg or current_leg['line'] != ref:
                    if current_leg: legs.append(current_leg)
                    current_leg = {
                        'type': ttype,
                        'line': ref,
                        'orig_id': prev_node,
                        'orig_stop': STOPS_CACHE[prev_node]['name'],
                        'dest_stop': STOPS_CACHE[node]['name'],
                        'time': w_edge
                    }
                else:
                    current_leg['dest_stop'] = STOPS_CACHE[node]['name']
                    current_leg['time'] += w_edge
                    
        for leg in legs:
            if 'time' in leg:
                leg['time'] = round(leg['time'])
                
        # Fetch ETA for all transit legs concurrently
        transit_legs = [(i, l) for i, l in enumerate(legs) if l['type'] not in ['walk']]
        pruned_any = False
        
        async def check_leg(leg):
            try:
                eta_res = await asyncio.wait_for(get_eta(leg['orig_id'], leg['type']), timeout=2.0)
                if eta_res.get('success') and not eta_res.get('cached'):
                    etas = [e for e in eta_res['data'] if str(e.get('line')) == str(leg['line'])]
                    if not etas:
                        return leg # This leg is dead!
                    return (leg, etas[0].get('eta'))
            except Exception as e:
                pass
            return None
            
        if transit_legs:
            results = await asyncio.gather(*(check_leg(l) for _, l in transit_legs))
            for i, res in enumerate(results):
                idx, leg = transit_legs[i]
                if res is leg: # Dead!
                    disabled_lines.add((leg['line'], leg['type']))
                    pruned_any = True
                elif isinstance(res, tuple):
                    _, eta_val = res
                    leg['live_eta'] = eta_val
                    if i == 0: # Only calculate wait_time for the very first transit leg
                        walk_time = sum(l['time'] for l in legs[:idx])
                        eta_mins = parse_time_str(eta_val)
                        if eta_mins != float('inf'):
                            wait_time = max(0, eta_mins - walk_time)
                            leg['wait_time'] = wait_time
                            
        if pruned_any:
            if not best_overall_route:
                best_overall_route = {"legs": legs, "route_ids": list(route_ids_used)}
            continue
                
        best_overall_route = {"legs": legs, "route_ids": list(route_ids_used)}
        break
        
    if not best_overall_route:
        return {"success": True, "routes": []}
        
    clean_legs = []
    for leg in best_overall_route['legs']:
        if leg['type'] == 'walk' and clean_legs and clean_legs[-1]['type'] == 'walk':
            clean_legs[-1]['time'] += leg['time']
            clean_legs[-1]['dest_stop'] = leg.get('dest_stop', 'destino')
        else:
            if leg['time'] > 0 or leg['type'] != 'walk': 
                clean_legs.append(leg)

    return {"success": True, "routes": [{"legs": clean_legs, "route_ids": best_overall_route['route_ids']}]}
"""

content = content.replace(old_func, new_func)

with open("main.py", "w") as f:
    f.write(content)
print("Patched main.py again")
