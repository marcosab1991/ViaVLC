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

conn = sqlite3.connect('stops.db')
c = conn.cursor()
c.execute("SELECT rowid, line, type, stops_json, geometry_json FROM line_routes")
rows = c.fetchall()

fixed = 0
for rowid, line, net_type, stops_json, geom_json in rows:
    seq = json.loads(stops_json)
    if not seq: continue
    
    stop_coords = []
    for sid in seq:
        c.execute("SELECT lat, lng, name FROM stops WHERE id=?", (str(sid),))
        res = c.fetchone()
        if res:
            stop_coords.append((res[0], res[1], res[2]))
            
    if not stop_coords: continue
    
    if geom_json:
        geom = json.loads(geom_json)
        coords = geom.get('coordinates', [])
        if coords and geom.get('type') != 'MultiLineString':
            geom_last_lon, geom_last_lat = coords[-1]
            geom_first_lon, geom_first_lat = coords[0]
            
            last_stop_lat, last_stop_lon, last_stop_name = stop_coords[-1]
            first_stop_lat, first_stop_lon, first_stop_name = stop_coords[0]
            
            dist_last = min(haversine(last_stop_lat, last_stop_lon, geom_last_lat, geom_last_lon),
                            haversine(last_stop_lat, last_stop_lon, geom_first_lat, geom_first_lon))
            
            dist_first = min(haversine(first_stop_lat, first_stop_lon, geom_first_lat, geom_first_lon),
                             haversine(first_stop_lat, first_stop_lon, geom_last_lat, geom_last_lon))
            
            # If the geometry misses both ends by >500m, or misses one end by >1000m
            if dist_last > 1000 or dist_first > 1000:
                print(f"Row {rowid} ({net_type} {line}): Geometry misses ends (first:{dist_first:.0f}m, last:{dist_last:.0f}m). Regenerating from stops.")
                new_coords = [[lon, lat] for lat, lon, name in stop_coords]
                geom['coordinates'] = new_coords
                c.execute("UPDATE line_routes SET geometry_json = ? WHERE rowid = ?", (json.dumps(geom), rowid))
                fixed += 1

conn.commit()
conn.close()
print(f"Done patching geometries. Fixed {fixed} rows.")
