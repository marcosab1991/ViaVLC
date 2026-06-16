import sqlite3
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

conn = sqlite3.connect('stops.db')
cursor = conn.cursor()
cursor.execute('SELECT id, name, lat, lng FROM stops')
stops = cursor.fetchall()

target_lat, target_lng = 39.47143278309849, -0.3798056624300355
print("Stops near 2290:")
for s in stops:
    dist = haversine(target_lat, target_lng, s[2], s[3])
    if dist < 50: # within 50 meters
        print(f"ID: {s[0]}, Name: {s[1]}, Dist: {dist:.1f}m, Coords: {s[2]}, {s[3]}")
