import sqlite3
import requests
import json
import unicodedata
from pyproj import Transformer

def remove_accents(input_str):
    if not input_str:
        return ""
    # Normalize unicode characters and remove diacritics
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

def init_db():
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    
    # Create table with spatial indexing in mind (lat/lng)
    c.execute('''
        CREATE TABLE IF NOT EXISTS stops (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            name_normalized TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            lines TEXT NOT NULL
        )
    ''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_lat_lng ON stops(lat, lng)')
    
    conn.commit()
    return conn, c

def fetch_emt_stops(c):
    print("Fetching EMT stops from Open Data...")
    url = "https://geoportal.valencia.es/server/rest/services/OPENDATA/Trafico/MapServer/226/query?where=1=1&outFields=*&f=json"
    response = requests.get(url, timeout=10)
    data = response.json()
    
    transformer = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)
    features = data.get("features", [])
    
    count = 0
    for feature in features:
        attr = feature.get("attributes", {})
        geom = feature.get("geometry", {})
        
        x, y = geom.get("x"), geom.get("y")
        if x and y:
            lng, lat = transformer.transform(x, y)
            stop_id = str(attr.get("id_parada"))
            name = attr.get("denominacion", f"Parada {stop_id}")
            lines = [l.strip() for l in str(attr.get("lineas", "")).replace("-", ",").split(",") if l.strip()]
            lines_json = json.dumps(lines)
            name_normalized = remove_accents(name)
            
            c.execute('INSERT OR REPLACE INTO stops (id, type, name, name_normalized, lat, lng, lines) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (stop_id, 'bus', name, name_normalized, lat, lng, lines_json))
            count += 1
            
    print(f"Inserted {count} EMT stops into DB.")

def fetch_metro_stops(c):
    print("Loading Metrovalencia stops...")
    try:
        with open('metro_stations.json', 'r', encoding='utf-8') as f:
            metro_stations = json.load(f)
            count = 0
            for stop in metro_stations:
                lines_json = json.dumps(stop.get("lines", []))
                name_normalized = remove_accents(stop["name"])
                
                lat = stop["location"]["lat"]
                lng = stop["location"]["lng"]
                
                # Check for overlap with bus stops (approx 20 meters)
                c.execute('SELECT COUNT(*) FROM stops WHERE type="bus" AND ABS(lat - ?) < 0.0002 AND ABS(lng - ?) < 0.0002', (lat, lng))
                if c.fetchone()[0] > 0:
                    # Offset slightly to the East to prevent z-index overlap on Leaflet
                    lng += 0.00025
                    lat -= 0.0001
                
                # Add metro- prefix to avoid ID collisions with EMT
                c.execute('INSERT OR REPLACE INTO stops (id, type, name, name_normalized, lat, lng, lines) VALUES (?, ?, ?, ?, ?, ?, ?)',
                          (f"metro-{stop['id']}", 'metro', stop["name"], name_normalized, lat, lng, lines_json))
                count += 1
            print(f"Inserted {count} Metrovalencia stops into DB.")
        
        # Load TRAM d'Alacant stops
        print("Loading TRAM d'Alacant stops...")
        with open('tram_stations.json', 'r', encoding='utf-8') as f:
            tram_stops = json.load(f)
            for stop in tram_stops:
                lines_json = json.dumps(stop.get('lines', []))
                name_normalized = remove_accents(stop['name'].lower())
                c.execute(
                    'INSERT OR REPLACE INTO stops (id, type, name, name_normalized, lat, lng, lines) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (f"tram-{stop['id']}", 'tram', stop['name'], name_normalized, stop['location']['lat'], stop['location']['lng'], lines_json)
                )
        print(f"Inserted {len(tram_stops)} TRAM d'Alacant stops into DB.")
    except Exception as e:
        print(f"Error loading Metrovalencia stops: {e}")

if __name__ == "__main__":
    conn, c = init_db()
    fetch_emt_stops(c)
    fetch_metro_stops(c)
    conn.commit()
    conn.close()
    print("Database initialization complete.")
