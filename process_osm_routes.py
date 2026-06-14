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
                    way_coords = []
                    for pt in member["geometry"]:
                        # GeoJSON uses [lng, lat].
                        way_coords.append([pt["lon"], pt["lat"]])
                    if way_coords:
                        geometry.append(way_coords)
            
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

print("Processing TRAM routes...")
tram_routes = process_routes("tram_routes.json", "tram")
print(f"Loaded {len(tram_routes)} TRAM routes.")

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

for r in emt_routes + metro_routes + tram_routes:
    # Build GeoJSON MultiLineString
    geojson = {
        "type": "MultiLineString",
        "coordinates": r["geometry"]
    }
    c.execute(
        "INSERT INTO routes (id, type, ref, destination, name, geometry_json) VALUES (?, ?, ?, ?, ?, ?)",
        (r["id"], r["type"], r["ref"], r["destination"], r["name"], json.dumps(geojson))
    )

conn.commit()
conn.close()
print("Done!")
