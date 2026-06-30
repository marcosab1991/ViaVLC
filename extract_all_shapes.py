import zipfile
import pandas as pd
import sqlite3
import json
import os
import shutil

FILES = {
    'metro': '20260619_140014_Metro_Valencia.zip',
    'tram': '20251222_070030_TRAM_Alicante.zip',
    'bus': '20260616_200012_EMT_Valencia.zip',
    'metrobus': '20260619_020006_GenValenciana_Interurbano.zip'
}

EXTRACT_DIR = "temp_gtfs"

def process_file(sys_type, filename, cursor):
    if not os.path.exists(filename):
        print(f"Skipping {sys_type}, file not found")
        return
        
    print(f"Processing {sys_type}...")
    if os.path.exists(EXTRACT_DIR):
        shutil.rmtree(EXTRACT_DIR)
    os.makedirs(EXTRACT_DIR)
    
    with zipfile.ZipFile(filename, 'r') as zip_ref:
        zip_ref.extractall(EXTRACT_DIR)
        
    try:
        shapes_df = pd.read_csv(os.path.join(EXTRACT_DIR, "shapes.txt"))
        trips_df = pd.read_csv(os.path.join(EXTRACT_DIR, "trips.txt"))
        routes_df = pd.read_csv(os.path.join(EXTRACT_DIR, "routes.txt"))
    except Exception as e:
        print(f"Error reading GTFS txt files for {sys_type}: {e}")
        return
        
    # Map route_id to short_name
    route_map = {}
    for _, r in routes_df.iterrows():
        sn = str(r.get('route_short_name', r.get('route_long_name', '')))
        # Fix Metrobus "L145" -> "145" for consistency
        if sys_type == 'metrobus' and sn.startswith('L'):
            sn = sn[1:]
        # FGV: filter Tram vs Metro if needed?
        # Actually FGV Metro zip only contains metro, FGV Tram zip only contains tram?
        # Let's check route_type just in case. FGV Metro zip might contain tram?
        # The zip name is Metro_Valencia. Actually Metro_Valencia has tram lines (4, 6, 8, 10).
        rtype = r.get('route_type', 3)
        actual_type = sys_type
        if sys_type == 'metro':
            actual_type = 'tram' if str(rtype) == '0' else 'metro'
            
        route_map[r['route_id']] = {'ref': sn, 'type': actual_type}
        
    # Get unique shapes and their route
    # We drop duplicate shape_id in trips to map shape_id -> route_id -> ref
    shape_to_trip = {}
    for _, t in trips_df.drop_duplicates('shape_id').iterrows():
        shape_to_trip[t['shape_id']] = {
            'route_id': t['route_id'],
            'headsign': str(t.get('trip_headsign', ''))
        }
        
    # Build geometries
    shapes_df = shapes_df.sort_values(['shape_id', 'shape_pt_sequence'])
    grouped = shapes_df.groupby('shape_id')
    
    inserted = 0
    for shape_id, group in grouped:
        trip_info = shape_to_trip.get(shape_id)
        if not trip_info: continue
        route_id = trip_info['route_id']
        if route_id not in route_map: continue
        
        info = route_map[route_id]
        dest = trip_info['headsign']
        
        coords = []
        for _, pt in group.iterrows():
            coords.append([pt['shape_pt_lon'], pt['shape_pt_lat']])
            
        geom = {
            "type": "LineString",
            "coordinates": coords
        }
        
        cursor.execute('''
            INSERT INTO routes (type, ref, destination, geometry_json)
            VALUES (?, ?, ?, ?)
        ''', (info['type'], info['ref'], dest, json.dumps(geom)))
        inserted += 1
        
    print(f"Inserted {inserted} shapes for {sys_type}")

def main():
    conn = sqlite3.connect('lines.db')
    c = conn.cursor()
    c.execute("DELETE FROM routes") # Clear old OSM geometries!
    
    for sys_type, filename in FILES.items():
        process_file(sys_type, filename, c)
        
    conn.commit()
    conn.close()
    
    if os.path.exists(EXTRACT_DIR):
        shutil.rmtree(EXTRACT_DIR)
    print("All GTFS shapes extracted successfully!")

if __name__ == "__main__":
    main()
