import json
import sqlite3
import os

def process_routes(file_name, transport_type):
    if not os.path.exists(file_name):
        return []
    
    with open(file_name, 'r') as f:
        data = json.load(f)
        
    routes = []
    for elem in data.get("elements", []):
        if elem["type"] == "relation":
            rel_id = elem["id"]
            tags = elem.get("tags", {})
            ref = tags.get("ref", "")
            destination = tags.get("to", "")
            name = tags.get("name", "")
            
            # Extract geometry
            geometry = []
            for member in elem.get("members", []):
                if member["type"] == "way" and "geometry" in member:
                    for pt in member["geometry"]:
                        # Leaflet prefers [lat, lng] for Polyline, but GeoJSON uses [lng, lat].
                        # In app.js we use L.geoJSON, so we should build a valid GeoJSON Feature!
                        geometry.append([pt["lon"], pt["lat"]])
            
            if geometry:
                routes.append({
                    "id": rel_id,
                    "type": transport_type,
                    "ref": ref,
                    "destination": destination,
                    "name": name,
                    "geometry": geometry
                })
    return routes

print("Processing EMT routes...")
emt_routes = process_routes("emt_routes.json", "bus")
print(f"Loaded {len(emt_routes)} EMT routes.")

print("Processing Metro routes...")
metro_routes = process_routes("metro_routes.json", "metro")
print(f"Loaded {len(metro_routes)} Metro routes.")

print("Saving to lines.db...")
conn = sqlite3.connect("lines.db")
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS routes (
        id INTEGER PRIMARY KEY,
        type TEXT,
        ref TEXT,
        destination TEXT,
        name TEXT,
        geometry_json TEXT
    )
''')
c.execute("DELETE FROM routes")

for r in emt_routes + metro_routes:
    # Build GeoJSON LineString
    geojson = {
        "type": "LineString",
        "coordinates": r["geometry"]
    }
    c.execute(
        "INSERT INTO routes (id, type, ref, destination, name, geometry_json) VALUES (?, ?, ?, ?, ?, ?)",
        (r["id"], r["type"], r["ref"], r["destination"], r["name"], json.dumps(geojson))
    )

conn.commit()
conn.close()
print("Done!")
