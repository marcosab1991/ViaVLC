import sqlite3
import json
import math

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def point_to_segment_distance(px, py, x1, y1, x2, y2):
    l2 = (x2 - x1)**2 + (y2 - y1)**2
    if l2 == 0: return math.hypot(px - x1, py - y1), 0.0
    t = max(0, min(1, ((px - x1)*(x2 - x1) + (py - y1)*(y2 - y1)) / l2))
    proj_x = x1 + t * (x2 - x1)
    proj_y = y1 + t * (y2 - y1)
    return math.hypot(px - proj_x, py - proj_y), t * math.sqrt(l2)

# Load all stops into memory
print("Loading stops...")
conn_stops = sqlite3.connect('stops.db')
c_stops = conn_stops.cursor()
c_stops.execute("SELECT id, name, type, lat, lng, lines FROM stops")
all_stops = []
for row in c_stops.fetchall():
    sid, sname, stype, slat, slng, lines_json = row
    lines = json.loads(lines_json) if lines_json else []
    all_stops.append({
        'id': sid, 'name': sname, 'type': stype, 
        'lat': slat, 'lng': slng, 'lines': lines
    })
print(f"Loaded {len(all_stops)} stops.")

print("Processing routes...")
conn_lines = sqlite3.connect('lines.db')
c_lines = conn_lines.cursor()

c_lines.execute("CREATE TABLE IF NOT EXISTS route_stops (route_id INTEGER PRIMARY KEY, stops_json TEXT)")
c_lines.execute("DELETE FROM route_stops")

c_lines.execute("SELECT id, type, ref, name, geometry_json FROM routes")
routes = c_lines.fetchall()

for route in routes:
    rid, rtype, ref, rname, geom_json = route
    geom = json.loads(geom_json)
    
    # Collect matching stops for this route
    route_stops = []
    
    search_ref = ref
    if rtype == 'metro' and not search_ref.startswith('L'):
        search_ref = f"L{search_ref}"
    elif rtype == 'tram' and not search_ref.startswith('L'):
        search_ref = f"L{search_ref}"
        
    for s in all_stops:
        if s['type'] == rtype:
            # Check if stop belongs to this line
            if search_ref in s['lines'] or ref in s['lines']:
                route_stops.append(s)
            elif rtype == 'metrobus' and search_ref:
                base_s_lines = ["".join([c for c in str(l) if c.isdigit()]) for l in s['lines']]
                base_ref = "".join([c for c in str(search_ref) if c.isdigit()])
                if base_ref and base_ref in base_s_lines:
                    route_stops.append(s)
    
    if not route_stops:
        continue
        
    # Sort route_stops using Nearest Neighbor algorithm
    max_d = -1
    start_stop = route_stops[0]
    for s1 in route_stops:
        for s2 in route_stops:
            d = haversine(s1['lat'], s1['lng'], s2['lat'], s2['lng'])
            if d > max_d:
                max_d = d
                start_stop = s1
                
    unvisited = route_stops[:]
    unvisited.remove(start_stop)
    ordered = [start_stop]
    
    while unvisited:
        curr = ordered[-1]
        closest = min(unvisited, key=lambda s: haversine(curr['lat'], curr['lng'], s['lat'], s['lng']))
        ordered.append(closest)
        unvisited.remove(closest)
        
    # Build results matching the required format
    results = []
    dist_along = 0.0
    for i, s in enumerate(ordered):
        if i > 0:
            dist_along += haversine(ordered[i-1]['lat'], ordered[i-1]['lng'], s['lat'], s['lng'])
        results.append({
            'id': s['id'],
            'name': s['name'],
            'dist_along': dist_along,
            'ortho_dist': 0.0
        })
    
    c_lines.execute("INSERT INTO route_stops (route_id, stops_json) VALUES (?, ?)", 
                   (rid, json.dumps(results)))

conn_lines.commit()
print("Done extracting sequences!")
