import sqlite3
import json

conn = sqlite3.connect('stops.db')
c = conn.cursor()

out_ids = ['metro-83', 'metro-84', 'metro-85', 'metro-86', 'metro-87', 'metro-88', 'metro-89', 'metro-90', 'metro-12', 'metro-91', 'metro-92', 'metro-93', 'metro-94', 'metro-95', 'metro-96', 'metro-97', 'metro-98', 'metro-100', 'metro-29', 'metro-101', 'metro-102', 'metro-103', 'metro-104', 'metro-105', 'metro-113', 'metro-112', 'metro-111', 'metro-110']
in_ids = ['metro-110', 'metro-111', 'metro-112', 'metro-113', 'metro-105', 'metro-104', 'metro-103', 'metro-102', 'metro-101', 'metro-29', 'metro-100', 'metro-98', 'metro-97', 'metro-96', 'metro-95', 'metro-94', 'metro-93', 'metro-92', 'metro-91', 'metro-12', 'metro-90', 'metro-89', 'metro-88', 'metro-87', 'metro-86', 'metro-85', 'metro-81', 'metro-82', 'metro-83']

c.execute("INSERT OR REPLACE INTO line_routes (type, line, direction, stops_json, geometry_json) VALUES ('tram', '4', 996, ?, NULL)", (json.dumps(out_ids),))
c.execute("INSERT OR REPLACE INTO line_routes (type, line, direction, stops_json, geometry_json) VALUES ('tram', '4', 997, ?, NULL)", (json.dumps(in_ids),))

conn.commit()
conn.close()
print("Injected perfect sequences for L4")
