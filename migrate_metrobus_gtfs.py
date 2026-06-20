import os
import zipfile
import csv
import sqlite3
import math
import json
from collections import defaultdict

GTFS_ZIP = '20260619_020006_GenValenciana_Interurbano.zip'
TMP_DIR = '/tmp/gtfs_metrobus'

def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def extract_gtfs():
    if not os.path.exists(TMP_DIR):
        os.makedirs(TMP_DIR)
    with zipfile.ZipFile(GTFS_ZIP, 'r') as zip_ref:
        zip_ref.extractall(TMP_DIR)

def migrate():
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    
    # Load existing Metrobus stops (Softour IDs)
    c.execute("SELECT id, lat, lng FROM stops WHERE type = 'metrobus'")
    softour_stops = c.fetchall()
    
    print(f"Loaded {len(softour_stops)} existing Softour stops.")
    
    # Read GTFS stops
    gtfs_stops = {}
    with open(os.path.join(TMP_DIR, 'stops.txt'), 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            gtfs_stops[row['stop_id']] = {
                'name': row.get('stop_name', ''),
                'lat': float(row['stop_lat']),
                'lng': float(row['stop_lon'])
            }
            
    print(f"Loaded {len(gtfs_stops)} GTFS stops.")
    
    # Match GTFS stops to Softour stops
    gtfs_to_softour = {}
    softour_matched = set()
    
    for gid, gdata in gtfs_stops.items():
        best_match = None
        best_dist = float('inf')
        for sid, slat, slng in softour_stops:
            if sid in softour_matched:
                continue
            dist = calculate_haversine(gdata['lat'], gdata['lng'], slat, slng)
            if dist < 150 and dist < best_dist:
                best_dist = dist
                best_match = sid
                
        if best_match:
            gtfs_to_softour[gid] = best_match
            softour_matched.add(best_match)
            # UPDATE coordinates
            c.execute("UPDATE stops SET lat=?, lng=? WHERE id=? AND type='metrobus'", (gdata['lat'], gdata['lng'], best_match))
    
    print(f"Matched {len(gtfs_to_softour)} stops! Updated their coordinates to official GTFS.")
    
    # Insert UNMATCHED GTFS stops
    unmatched = 0
    for gid, gdata in gtfs_stops.items():
        if gid not in gtfs_to_softour:
            new_id = f"gtfs-{gid}"
            gtfs_to_softour[gid] = new_id
            try:
                import unicodedata
                name_norm = ''.join(c for c in unicodedata.normalize('NFD', gdata['name']) if unicodedata.category(c) != 'Mn').lower()
                c.execute("INSERT INTO stops (id, name, name_normalized, lat, lng, type, lines) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (new_id, gdata['name'], name_norm, gdata['lat'], gdata['lng'], 'metrobus', '[]'))
                unmatched += 1
            except sqlite3.IntegrityError as e:
                print(f"Error inserting {new_id}: {e}")
    
    print(f"Inserted {unmatched} unmatched GTFS stops.")
    
    # Read routes
    routes = {}
    with open(os.path.join(TMP_DIR, 'routes.txt'), 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            # Short name is line number (e.g. 150)
            routes[row['route_id']] = row.get('route_short_name') or row.get('route_long_name', 'MB')
            
    # Read trips
    trips_to_route = {}
    trips_to_shape = {}
    with open(os.path.join(TMP_DIR, 'trips.txt'), 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            trips_to_route[row['trip_id']] = row['route_id']
            if 'shape_id' in row and row['shape_id']:
                trips_to_shape[row['trip_id']] = row['shape_id']
                
    import pandas as pd
    
    # Read shapes (geometry)
    shapes = defaultdict(list)
    shapes_path = os.path.join(TMP_DIR, 'shapes.txt')
    if os.path.exists(shapes_path):
        df_shapes = pd.read_csv(shapes_path)
        df_shapes = df_shapes.sort_values(by=['shape_id', 'shape_pt_sequence'])
        for row in df_shapes.itertuples(index=False):
            shapes[str(row.shape_id)].append([float(row.shape_pt_lat), float(row.shape_pt_lon)])

    # Read stop_times
    trip_stops = defaultdict(list)
    df_st = pd.read_csv(os.path.join(TMP_DIR, 'stop_times.txt'), dtype={'stop_id': str, 'trip_id': str})
    df_st = df_st.sort_values(by=['trip_id', 'stop_sequence'])
    for row in df_st.itertuples(index=False):
        trip_stops[str(row.trip_id)].append(str(row.stop_id))

    # Delete existing Metrobus line routes
    c.execute("DELETE FROM line_routes WHERE type = 'metrobus'")
    
    # Build unique sequence per route/direction
    # We will pick the longest trip for each route/direction
    route_sequences = defaultdict(list)
    for trip_id, stops in trip_stops.items():
        route_id = trips_to_route.get(trip_id)
        if not route_id: continue
        line_ref = routes[route_id]
        
        # Translate stop IDs
        mapped_stops = [gtfs_to_softour[s] for s in stops if s in gtfs_to_softour]
        if len(mapped_stops) < 2: continue
        
        # Determine direction based on start/end stops
        direction = f"{mapped_stops[0]}-{mapped_stops[-1]}"
        key = (line_ref, direction)
        
        if len(mapped_stops) > len(route_sequences[key]):
            route_sequences[key] = mapped_stops

    # Write to line_routes
    inserted_lines = set()
    for (line_ref, direction), seq in route_sequences.items():
        ordered_stops_json = json.dumps(seq)
        c.execute("INSERT INTO line_routes (line, type, direction, stops_json) VALUES (?, ?, ?, ?)",
                  (line_ref, 'metrobus', direction, ordered_stops_json))
        inserted_lines.add(line_ref)
        
    print(f"Inserted line_routes for {len(inserted_lines)} unique Metrobus lines.")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    extract_gtfs()
    migrate()
