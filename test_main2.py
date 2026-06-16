import json
import sqlite3

conn = sqlite3.connect('lines.db')
cursor = conn.cursor()
cursor.execute('SELECT destination, geometry_json FROM routes WHERE ref=? AND type=?', ('4', 'metro'))
rows = cursor.fetchall()

best_geom = None
highest_ratio = -1
target_dest = "parc cientific"

import difflib

for row_dest, geom_json in rows:
    db_dest = row_dest.lower()
    ratio = difflib.SequenceMatcher(None, db_dest, target_dest).ratio()
    if ratio > highest_ratio:
        highest_ratio = ratio
        best_geom = geom_json

if best_geom:
    geom = json.loads(best_geom)
    print("Geom type:", geom.get("type"))
    coords = geom.get("coordinates", [])
    print("Coords length:", len(coords))
    if len(coords) > 0:
        print("First coord sample:", coords[0][0])
else:
    print("NO BEST GEOM")
