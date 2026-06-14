import sqlite3
import json
import math

# Haversine distance formula
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000 # Radius of earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Connect to DBs
lines_conn = sqlite3.connect('lines.db')
lines_c = lines_conn.cursor()
stops_conn = sqlite3.connect('stops.db')
stops_c = stops_conn.cursor()

# Get all TRAM lines
lines_c.execute("SELECT ref, geometry_json FROM routes WHERE type='tram'")
routes = lines_c.fetchall()
tram_lines = []
for ref, geo_json in routes:
    geo = json.loads(geo_json)
    coords = geo.get('coordinates', [])
    tram_lines.append({
        'ref': ref,
        'coords': coords
    })

# Get all TRAM stops
stops_c.execute("SELECT id, lat, lng, lines FROM stops WHERE type='tram'")
stops = stops_c.fetchall()

updated = 0
for sid, lat, lng, lines_json in stops:
    current_lines = json.loads(lines_json) if lines_json else []
    
    for route in tram_lines:
        ref = route['ref']
        if ref in current_lines:
            continue
            
        # Check distance to route coordinates
        min_dist = float('inf')
        for way in route['coords']:
            # Handle both LineString and MultiLineString
            if len(way) > 0 and isinstance(way[0], (int, float)):
                lon2, lat2 = way
                dist = haversine(lat, lng, lat2, lon2)
                if dist < min_dist:
                    min_dist = dist
            else:
                for lon2, lat2 in way:
                    dist = haversine(lat, lng, lat2, lon2)
                    if dist < min_dist:
                        min_dist = dist
                
        # If stop is within 100 meters of the line, associate it!
        if min_dist < 100:
            current_lines.append(ref)
            
    # Remove duplicates and sort
    new_lines = sorted(list(set(current_lines)))
    
    if new_lines != json.loads(lines_json):
        stops_c.execute("UPDATE stops SET lines=? WHERE id=?", (json.dumps(new_lines), sid))
        updated += 1
        print(f"Updated {sid} with lines: {new_lines}")

stops_conn.commit()
print(f"Updated {updated} TRAM stops with line data!")
