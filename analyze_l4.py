import sqlite3
import json
import math

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

conn = sqlite3.connect('lines.db')
cursor = conn.cursor()
cursor.execute("SELECT geometry_json FROM routes WHERE ref='4' AND type='metro'")
rows = cursor.fetchall()

merged = []
for row in rows:
    geom = json.loads(row[0])
    if geom.get("type") == "MultiLineString":
        merged.extend(geom.get("coordinates", []))

print(f"Total ways: {len(merged)}")
long_segments = 0
for i, way in enumerate(merged):
    for j in range(len(way) - 1):
        dist = haversine(way[j][1], way[j][0], way[j+1][1], way[j+1][0])
        if dist > 300: # 300 meters
            print(f"Way {i} segment {j} is {dist:.0f}m long! {way[j]} -> {way[j+1]}")
            long_segments += 1

print(f"Found {long_segments} long segments.")
