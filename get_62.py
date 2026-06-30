import sqlite3
import json

c = sqlite3.connect('stops.db')
route = c.execute("SELECT stops_json FROM line_routes WHERE type='bus' AND line='62'").fetchone()
stops = json.loads(route[0])
print(f"Total stops on 62: {len(stops)}")
for sid in stops:
    s = c.execute("SELECT name FROM stops WHERE id=?", (str(sid),)).fetchone()
    if s and ('xativa' in s[0].lower() or 'nord' in s[0].lower() or 'espanya' in s[0].lower()):
        print(sid, s[0])
