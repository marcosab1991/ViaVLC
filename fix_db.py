import sqlite3
import json

conn = sqlite3.connect('stops.db')
c = conn.cursor()

# Create new table
c.execute('''
    CREATE TABLE line_routes_new (
        line TEXT,
        direction INTEGER,
        stops_json TEXT,
        type TEXT,
        PRIMARY KEY (line, direction, type)
    )
''')

# Copy existing EMT data (assuming they are bus)
try:
    c.execute('INSERT INTO line_routes_new (line, direction, stops_json, type) SELECT line, direction, stops_json, COALESCE(type, "bus") FROM line_routes')
except Exception as e:
    print(e)
    
c.execute('DROP TABLE line_routes')
c.execute('ALTER TABLE line_routes_new RENAME TO line_routes')

conn.commit()
conn.close()
print("Fixed PK")
