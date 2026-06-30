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
c.execute("SELECT rowid, stops_json, geometry_json FROM line_routes WHERE type IN ('metro', 'tram') AND line='4'")
rows = c.fetchall()

for rowid, stops_json, geom_json in rows:
    seq = json.loads(stops_json)
    if not seq: continue
    
    # Get all stop coordinates for this sequence
    stop_coords = []
    for sid in seq:
        c.execute("SELECT lat, lng, name FROM stops WHERE id=?", (str(sid),))
        res = c.fetchone()
        if res:
            stop_coords.append((res[0], res[1], res[2]))
            
    if not stop_coords: continue
    
    # Check if the existing geometry covers the last stop
    if geom_json:
        geom = json.loads(geom_json)
        coords = geom.get('coordinates', [])
        if coords:
            geom_last_lon, geom_last_lat = coords[-1]
            geom_first_lon, geom_first_lat = coords[0]
            
            # Distance from sequence last stop to geometry last point
            last_stop_lat, last_stop_lon, last_stop_name = stop_coords[-1]
            first_stop_lat, first_stop_lon, first_stop_name = stop_coords[0]
            
            dist_last = haversine(last_stop_lat, last_stop_lon, geom_last_lat, geom_last_lon)
            dist_first = haversine(first_stop_lat, first_stop_lon, geom_first_lat, geom_first_lon)
            
            # If the geometry misses the endpoint by more than 500m, it's the wrong shape!
            if dist_last > 500 and dist_first > 500:
                print(f"Row {rowid}: Geometry completely wrong! Generating from stops.")
                new_coords = [[lon, lat] for lat, lon, name in stop_coords]
                geom['coordinates'] = new_coords
                c.execute("UPDATE line_routes SET geometry_json = ? WHERE rowid = ?", (json.dumps(geom), rowid))
            elif dist_last > 500:
                print(f"Row {rowid}: Geometry misses end ({last_stop_name}) by {dist_last:.0f}m. Appending stops.")
                # Append missing stops
                for lat, lon, name in stop_coords:
                    # If this stop is far from the current geometry end, append it
                    # (This is a bit crude, a better way is to just use the stops for the diverging part)
                    if haversine(lat, lon, geom_last_lat, geom_last_lon) > 200:
                        # Actually, to be safe and avoid weird zig-zags, if it's a branch, let's just 
                        # regenerate the WHOLE geometry from the stops!
                        pass
                
                print(f"Regenerating entirely from stops for Row {rowid}")
                new_coords = [[lon, lat] for lat, lon, name in stop_coords]
                geom['coordinates'] = new_coords
                c.execute("UPDATE line_routes SET geometry_json = ? WHERE rowid = ?", (json.dumps(geom), rowid))

conn.commit()
conn.close()
print("Done patching L4.")
