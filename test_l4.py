import sqlite3
import json

line = "4"
dest = "Dr. Lluch"
type_ = "metro"

conn = sqlite3.connect('lines.db')
c = conn.cursor()
c.execute("SELECT destination, geometry_json FROM routes WHERE ref=? AND type=?", (line, type_))
rows = c.fetchall()

best_geom = None
highest_ratio = -1

import difflib

for row_dest, geom_json in rows:
    ratio = difflib.SequenceMatcher(None, dest.lower(), row_dest.lower()).ratio()
    if ratio > highest_ratio:
        highest_ratio = ratio
        best_geom = geom_json

geom_obj = json.loads(best_geom)
print("Type:", geom_obj["type"])
print("Num ways:", len(geom_obj["coordinates"]))

# Print start/end of some ways
for i, way in enumerate(geom_obj["coordinates"][:5]):
    print(f"Way {i}: {len(way)} pts. Start: {way[0]} End: {way[-1]}")
