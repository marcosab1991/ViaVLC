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

with open("metro_routes.json", "r") as f:
    data = json.load(f)

for elem in data.get("elements", []):
    if elem["type"] == "relation" and elem.get("tags", {}).get("ref") in ("4", "6"):
        print(f"Checking rel {elem['id']} {elem['tags']['name']}")
        ways = []
        for member in elem.get("members", []):
            if member["type"] == "way" and "geometry" in member:
                way_coords = [(pt["lon"], pt["lat"]) for pt in member["geometry"]]
                ways.append(way_coords)
        
        # Check inside each way
        for i, w in enumerate(ways):
            for j in range(len(w) - 1):
                dist = haversine(w[j][1], w[j][0], w[j+1][1], w[j+1][0])
                if dist > 500: # jump > 500 meters
                    print(f"  Way {i} jump: {dist:.0f}m between {w[j]} and {w[j+1]}")
