import os
import zipfile
import urllib.request
import pandas as pd
import sqlite3
import json

GTFS_URL = "https://opendata.vlci.valencia.es:8443/dataset/4645f8bf-28d7-4420-bab2-d5c5e7de2a5a/resource/11591648-a984-4d64-89e3-3730f3123403/download/googletransit.zip"
ZIP_PATH = "googletransit.zip"
EXTRACT_DIR = "gtfs_data"

def download_and_extract():
    if not os.path.exists(ZIP_PATH):
        print("GTFS zip not found locally! Please place it as googletransit.zip")
        return
    
    if not os.path.exists(EXTRACT_DIR):
        print("Extracting GTFS...")
        with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
            zip_ref.extractall(EXTRACT_DIR)
        print("Extraction complete.")

def process_gtfs():
    print("Processing GTFS data...")
    routes_df = pd.read_csv(os.path.join(EXTRACT_DIR, "routes.txt"))
    trips_df = pd.read_csv(os.path.join(EXTRACT_DIR, "trips.txt"))
    stop_times_df = pd.read_csv(os.path.join(EXTRACT_DIR, "stop_times.txt"))
    
    # Map route_id to route_short_name
    route_map = dict(zip(routes_df['route_id'], routes_df['route_short_name']))
    
    # Get the trip with max stops for each route and shape_id
    trip_counts = stop_times_df.groupby('trip_id').size().reset_index(name='stop_count')
    trips_with_counts = pd.merge(trips_df, trip_counts, on='trip_id')
    
    # Sort by stop_count descending, then drop duplicates by route_id and shape_id
    longest_trips = trips_with_counts.sort_values('stop_count', ascending=False).drop_duplicates(['route_id', 'shape_id'])
    
    line_routes = []
    
    # We need to assign an artificial direction_id since EMT doesn't provide one
    route_directions = {}
    
    for _, row in longest_trips.iterrows():
        trip_id = row['trip_id']
        route_id = row['route_id']
        shape_id = row.get('shape_id', '')
        route_short_name = str(route_map.get(route_id, ""))
        
        # Assign 0 or 1 sequentially
        if route_short_name not in route_directions:
            route_directions[route_short_name] = 0
        direction_id = route_directions[route_short_name]
        route_directions[route_short_name] += 1
        
        # Get stop_times for this trip, ordered by stop_sequence
        trip_stops = stop_times_df[stop_times_df['trip_id'] == trip_id].sort_values('stop_sequence')
        
        stop_ids = trip_stops['stop_id'].astype(str).tolist()
        
        line_routes.append({
            "line": route_short_name,
            "direction": direction_id,
            "stops": stop_ids
        })
        
    print(f"Processed {len(line_routes)} line routes.")
    return line_routes

def save_to_db(line_routes):
    print("Saving to stops.db...")
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS line_routes (
            line TEXT,
            direction INTEGER,
            stops_json TEXT,
            PRIMARY KEY (line, direction)
        )
    ''')
    
    c.execute('DELETE FROM line_routes')
    
    for route in line_routes:
        c.execute('INSERT INTO line_routes (line, direction, stops_json) VALUES (?, ?, ?)',
                  (route['line'], route['direction'], json.dumps(route['stops'])))
        
    conn.commit()
    conn.close()
    print("Database updated successfully.")

if __name__ == "__main__":
    try:
        download_and_extract()
        routes = process_gtfs()
        save_to_db(routes)
    except Exception as e:
        print(f"Error: {e}")
