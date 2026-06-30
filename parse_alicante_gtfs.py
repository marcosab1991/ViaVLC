import os
import zipfile
import sqlite3
import pandas as pd
import json
import math
from collections import defaultdict

ZIP_PATH = "20251222_070030_TRAM_Alicante.zip"
TMP_DIR = "/tmp/gtfs_alicante"

def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def main():
    if not os.path.exists(TMP_DIR):
        os.makedirs(TMP_DIR)
        with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
            zip_ref.extractall(TMP_DIR)
            
        
    with zipfile.ZipFile(ZIP_PATH, 'r') as z:
        for f in ['stops.txt', 'stop_times.txt', 'trips.txt', 'routes.txt']:
            z.extract(f, TMP_DIR)
            
    # 3. Load Data
    stops_df = pd.read_csv(os.path.join(TMP_DIR, "stops.txt"), dtype={'stop_id': str})
    stop_times_df = pd.read_csv(os.path.join(TMP_DIR, "stop_times.txt"), dtype={'stop_id': str})
    trips_df = pd.read_csv(os.path.join(TMP_DIR, "trips.txt"))
    routes_df = pd.read_csv(os.path.join(TMP_DIR, "routes.txt"))
    
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    
    # 1. Clean up existing Alicante data
    print("Removing old tram_alicante data...")
    c.execute("DELETE FROM stops WHERE type = 'tram_alicante'")
    c.execute("DELETE FROM line_routes WHERE type = 'tram_alicante'")
    
    import unicodedata
    def remove_accents(input_str):
        if not input_str:
            return ""
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return u"".join([c for c in nfkd_form if not unicodedata.combining(c)])

    # 5. Build Routes and Stop Lines
    print("Building line routes...")
    # Map route_id to short_name (e.g. '9' instead of 'L9' internally)
    route_map = dict(zip(routes_df['route_id'].astype(str), routes_df['route_short_name'].astype(str)))
    trip_to_route = dict(zip(trips_df['trip_id'].astype(str), trips_df['route_id'].astype(str)))
    
    # Group stops by trip and compute stop to lines
    trip_stops_dict = {}
    stop_to_lines = defaultdict(set)
    stop_times_df = stop_times_df.sort_values(['trip_id', 'stop_sequence'])
    for trip_id, group in stop_times_df.groupby('trip_id'):
        trip_id_str = str(trip_id)
        stops = group['stop_id'].astype(str).tolist()
        trip_stops_dict[trip_id_str] = stops
        
        if trip_id_str in trip_to_route:
            route_id = trip_to_route[trip_id_str]
            if route_id in route_map:
                line_name = f"L{route_map[route_id]}"
                for stop in stops:
                    stop_to_lines[stop].add(line_name)

    # 4. Insert fresh stops
    print(f"Inserting {len(stops_df)} Alicante stops...")
    for idx, row in stops_df.iterrows():
        stop_id_str = str(row['stop_id'])
        new_id = f"tram_alicante-{stop_id_str}"
        name = row['stop_name']
        lat = float(row['stop_lat'])
        lon = float(row['stop_lon'])
        
        lines_json = json.dumps(list(stop_to_lines.get(stop_id_str, set())))
        
        c.execute("INSERT INTO stops (id, name, lat, lng, type, name_normalized, lines) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (new_id, name, lat, lon, 'tram_alicante', remove_accents(name).lower(), lines_json))
        
    # Group by route and direction to find unique patterns
    routes_patterns = defaultdict(list)
    for trip_id, stops in trip_stops_dict.items():
        if trip_id not in trip_to_route: continue
        route_id = trip_to_route[trip_id]
        if route_id not in route_map: continue
        line_name = route_map[route_id]
        
        db_stop_ids = [f"tram_alicante-{sid}" for sid in stops]
        if len(db_stop_ids) > 1:
            direction = f"{db_stop_ids[0]}-{db_stop_ids[-1]}"
            routes_patterns[line_name].append({
                "direction": direction,
                "stops": db_stop_ids,
                "count": len(db_stop_ids)
            })
            
    # Insert longest trips for each direction
    for line_name, patterns in routes_patterns.items():
        # Group by direction
        dir_dict = defaultdict(list)
        for p in patterns:
            dir_dict[p['direction']].append(p)
            
        for direction, dir_patterns in dir_dict.items():
            best_pattern = max(dir_patterns, key=lambda x: x['count'])
            c.execute("INSERT INTO line_routes (line, direction, stops_json, type) VALUES (?, ?, ?, ?)",
                     (line_name, direction, json.dumps(best_pattern['stops']), 'tram_alicante'))
                     
    conn.commit()
    conn.close()
    print("Alicante Tram GTFS parsing complete! (Run add_shapes_to_db.py next to add geometries)")

if __name__ == "__main__":
    main()
