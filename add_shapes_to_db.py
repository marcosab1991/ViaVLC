import os
import zipfile
import sqlite3
import pandas as pd
import json
from collections import defaultdict

# Setup paths
GTFS_ZIPS = {
    'bus': '20260616_200012_EMT_Valencia.zip',
    'metro': '20260619_140014_Metro_Valencia.zip',
    'metrobus': '20260619_020006_GenValenciana_Interurbano.zip',
    'tram_alicante': '20251222_070030_TRAM_Alicante.zip'
}

TMP_DIR = "/tmp/gtfs_shapes_temp"

def process_gtfs_shapes(zip_path, network_type, db_cursor):
    print(f"Processing {network_type} from {zip_path}...")
    
    # Extract needed files
    extract_dir = os.path.join(TMP_DIR, network_type)
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)
        with zipfile.ZipFile(zip_path, 'r') as z:
            # We only need routes.txt, trips.txt, stop_times.txt, shapes.txt
            for file in ['routes.txt', 'trips.txt', 'stop_times.txt', 'shapes.txt']:
                if file in z.namelist():
                    z.extract(file, extract_dir)
    
    # Check if shapes exist
    shapes_file = os.path.join(extract_dir, "shapes.txt")
    if not os.path.exists(shapes_file):
        print(f"No shapes.txt found for {network_type}!")
        return

    # Load data
    routes_df = pd.read_csv(os.path.join(extract_dir, "routes.txt"))
    trips_df = pd.read_csv(os.path.join(extract_dir, "trips.txt"))
    stop_times_df = pd.read_csv(os.path.join(extract_dir, "stop_times.txt"))
    shapes_df = pd.read_csv(shapes_file)
    
    # Build shapes dictionary (LineString coordinates)
    print(f"Building shapes dictionary for {network_type}...")
    shapes_dict = defaultdict(list)
    shapes_df = shapes_df.sort_values(by=['shape_id', 'shape_pt_sequence'])
    for row in shapes_df.itertuples(index=False):
        shapes_dict[str(row.shape_id)].append([float(row.shape_pt_lon), float(row.shape_pt_lat)])
        
    # Map route_id to short_name
    route_map = dict(zip(routes_df['route_id'].astype(str), routes_df['route_short_name'].astype(str)))
    
    # Build a map of trip_id to shape_id and route short_name
    trip_shapes = {}
    trip_routes = {}
    for row in trips_df.itertuples(index=False):
        tid = str(row.trip_id)
        if pd.notna(row.shape_id):
            if isinstance(row.shape_id, float):
                trip_shapes[tid] = str(int(row.shape_id))
            else:
                trip_shapes[tid] = str(row.shape_id)
        route_id_str = str(row.route_id)
        if route_id_str in route_map:
            trip_routes[tid] = route_map[route_id_str]
        else:
            trip_routes[tid] = route_id_str # Fallback to route_id
            
    # Count stops per trip to find the longest trip for each route & shape
    trip_counts = stop_times_df.groupby('trip_id').size().reset_index(name='stop_count')
    trip_counts['trip_id'] = trip_counts['trip_id'].astype(str)
    
    longest_trips = {}
    
    for row in trip_counts.itertuples(index=False):
        tid = str(row.trip_id)
        if tid not in trip_shapes or tid not in trip_routes:
            continue
            
        shape_id = trip_shapes[tid]
        short_name = trip_routes[tid]
        
        key = (short_name, shape_id)
        
        if key not in longest_trips or longest_trips[key]['count'] < row.stop_count:
            longest_trips[key] = {
                'trip_id': tid,
                'count': row.stop_count,
                'shape_id': shape_id,
                'line': short_name
            }
            
    print(f"Found {len(longest_trips)} representative shapes for {network_type} lines.")
    
    # Now update the database!
    updated_count = 0
    
    # We will fetch all existing routes for this type
    # Note: Metrovalencia GTFS contains BOTH 'metro' and 'tram'
    type_query = "type IN ('metro', 'tram')" if network_type == "metro" else "type = ?"
    params = () if network_type == "metro" else (network_type,)
    
    db_cursor.execute(f"SELECT rowid, line, stops_json FROM line_routes WHERE {type_query}", params)
    db_routes = db_cursor.fetchall()
    
    import math
    def calculate_haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

    for rowid, db_line, stops_json in db_routes:
        best_shape_id = None
        best_distance = float('inf')
        
        # Get the first stop of this route
        try:
            seq = json.loads(stops_json)
            first_stop_id = str(seq[0])
            db_cursor.execute("SELECT lat, lng FROM stops WHERE id=?", (first_stop_id,))
            res = db_cursor.fetchone()
            if not res: continue
            first_lat, first_lng = res
        except Exception:
            continue
            
        for (line_name, shape_id), trip_info in longest_trips.items():
            match_name = line_name
            if network_type == "metrobus" and not line_name.startswith("L"):
                match_name = f"L{line_name}"
                
            if match_name == db_line and shape_id in shapes_dict:
                shape_coords = shapes_dict[shape_id]
                if not shape_coords: continue
                
                # Get the first coordinate of the shape
                shape_first_lng, shape_first_lat = shape_coords[0]
                
                dist = calculate_haversine(first_lat, first_lng, shape_first_lat, shape_first_lng)
                if dist < best_distance:
                    best_distance = dist
                    best_shape_id = shape_id
                    
        # If we found a matching shape and it's reasonably close (e.g. within 5km)
        if best_shape_id and best_distance < 5000:
            geom_json = json.dumps({
                "type": "LineString",
                "coordinates": shapes_dict[best_shape_id]
            })
            db_cursor.execute("UPDATE line_routes SET geometry_json = ? WHERE rowid = ?", (geom_json, rowid))
            updated_count += 1
            
    print(f"Updated {updated_count} routes in DB for {network_type}.")

def main():
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    
    try:
        c.execute('ALTER TABLE line_routes ADD COLUMN geometry_json TEXT')
    except sqlite3.OperationalError:
        pass
        
    for net_type, zip_file in GTFS_ZIPS.items():
        if os.path.exists(zip_file):
            process_gtfs_shapes(zip_file, net_type, c)
        else:
            print(f"Warning: {zip_file} not found, skipping {net_type}.")
            
    # --- ARTIFICIAL GEOMETRY PATCH ---
    # The official GTFS shapes for TRAM d'Alacant don't accurately reach Benidorm Intermodal.
    # L1 ends at Benidorm (-0.134754) and L9 starts slightly east.
    # We artificially append Benidorm Intermodal (-0.1229719967, 38.548500061) to their shapes
    # so they connect visually on the map.
    intermodal_coords = [-0.1229719967, 38.548500061]
    
    c.execute('SELECT rowid, geometry_json FROM line_routes WHERE type = "tram_alicante" AND line = "1"')
    rows = c.fetchall()
    for rowid, geom_str in rows:
        if not geom_str: continue
        geom = json.loads(geom_str)
        coords = geom.get('coordinates', [])
        if not coords: continue
        if abs(coords[0][0] - (-0.134754)) < 0.01:
            coords.insert(0, intermodal_coords)
        elif abs(coords[-1][0] - (-0.134754)) < 0.01:
            coords.append(intermodal_coords)
        geom['coordinates'] = coords
        c.execute('UPDATE line_routes SET geometry_json = ? WHERE rowid = ?', (json.dumps(geom), rowid))

    c.execute('SELECT rowid, geometry_json FROM line_routes WHERE type = "tram_alicante" AND line = "9"')
    rows = c.fetchall()
    for rowid, geom_str in rows:
        if not geom_str: continue
        geom = json.loads(geom_str)
        coords = geom.get('coordinates', [])
        if not coords: continue
        if abs(coords[0][0] - (-0.124807)) < 0.01:
            coords.insert(0, intermodal_coords)
        elif abs(coords[-1][0] - (-0.124807)) < 0.01:
            coords.append(intermodal_coords)
        geom['coordinates'] = coords
        c.execute('UPDATE line_routes SET geometry_json = ? WHERE rowid = ?', (json.dumps(geom), rowid))
    # ----------------------------------

    conn.commit()
    conn.close()
    print("Done generating all GTFS shapes!")

if __name__ == "__main__":
    main()
