import zipfile
import pandas as pd
import sqlite3
import json
import math
import os

ZIP_PATH = "20260619_140014_Metro_Valencia.zip"
EXTRACT_DIR = "gtfs_metro_data"

def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def main():
    if not os.path.exists(EXTRACT_DIR):
        with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
            zip_ref.extractall(EXTRACT_DIR)
            
    print("Loading GTFS...")
    routes_df = pd.read_csv(os.path.join(EXTRACT_DIR, "routes.txt"))
    trips_df = pd.read_csv(os.path.join(EXTRACT_DIR, "trips.txt"))
    stop_times_df = pd.read_csv(os.path.join(EXTRACT_DIR, "stop_times.txt"))
    stops_df = pd.read_csv(os.path.join(EXTRACT_DIR, "stops.txt"))
    
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    c.execute("SELECT id, lat, lng FROM stops WHERE type IN ('metro', 'tram')")
    db_stops = c.fetchall()
    
    print("Mapping GTFS stops to DB stops...")
    gtfs_to_db = {}
    unmapped = 0
    for _, row in stops_df.iterrows():
        best_d = float('inf')
        best_id = None
        for db_id, db_lat, db_lng in db_stops:
            d = calculate_haversine(row['stop_lat'], row['stop_lon'], db_lat, db_lng)
            if d < best_d:
                best_d = d
                best_id = db_id
        if best_d < 150: # 150 meters tolerance
            gtfs_to_db[str(row['stop_id'])] = best_id
        else:
            unmapped += 1
            
    print(f"Mapped {len(gtfs_to_db)} stops. Unmapped: {unmapped}")
    
    route_map = dict(zip(routes_df['route_id'], zip(routes_df['route_short_name'], routes_df['route_type'])))
    
    # route_type: 1 = subway (metro), 0 = tram
    
    trip_counts = stop_times_df.groupby('trip_id').size().reset_index(name='stop_count')
    trips_with_counts = pd.merge(trips_df, trip_counts, on='trip_id')
    longest_trips = trips_with_counts.sort_values('stop_count', ascending=False).drop_duplicates(['route_id', 'shape_id'])
    
    line_routes = []
    route_directions = {}
    
    for _, row in longest_trips.iterrows():
        trip_id = row['trip_id']
        route_id = row['route_id']
        shape_id = row.get('shape_id', '')
        short_name, rtype = route_map.get(route_id, ("", 1))
        
        # Metro is route_type 1 in GTFS, Tram is 0
        sys_type = 'tram' if str(rtype) == '0' else 'metro'
        
        if short_name not in route_directions:
            route_directions[short_name] = 0
        direction_id = route_directions[short_name]
        route_directions[short_name] += 1
        
        trip_stops = stop_times_df[stop_times_df['trip_id'] == trip_id].sort_values('stop_sequence')
        
        db_stop_ids = []
        for sid in trip_stops['stop_id']:
            mapped = gtfs_to_db.get(str(sid))
            if mapped:
                db_stop_ids.append(mapped)
                
        if len(db_stop_ids) > 1:
            line_routes.append({
                "line": str(short_name),
                "direction": direction_id,
                "stops": db_stop_ids,
                "type": sys_type
            })
            
    print(f"Deleting old OSM lines from lines.db to avoid duplication...")
    conn2 = sqlite3.connect('lines.db')
    conn2.execute("DELETE FROM route_stops WHERE route_id IN (SELECT id FROM routes WHERE type IN ('metro', 'tram'))")
    conn2.execute("DELETE FROM routes WHERE type IN ('metro', 'tram')")
    conn2.commit()
    conn2.close()
    
    print("Inserting into line_routes...")
    # Clean old GTFS lines if any
    c.execute("DELETE FROM line_routes WHERE type IN ('metro', 'tram')")
    
    for route in line_routes:
        # Check if type column exists
        try:
            c.execute('INSERT INTO line_routes (line, direction, stops_json, type) VALUES (?, ?, ?, ?)',
                      (route['line'], route['direction'], json.dumps(route['stops']), route['type']))
        except sqlite3.OperationalError:
            print("Adding type column to line_routes...")
            c.execute('ALTER TABLE line_routes ADD COLUMN type TEXT')
            # Set all existing (EMT) to 'bus'
            c.execute('UPDATE line_routes SET type = "bus" WHERE type IS NULL')
            c.execute('INSERT INTO line_routes (line, direction, stops_json, type) VALUES (?, ?, ?, ?)',
                      (route['line'], route['direction'], json.dumps(route['stops']), route['type']))
            
    conn.commit()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    main()
