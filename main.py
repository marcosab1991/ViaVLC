import asyncio
import json
import math
import re
import urllib.request
import difflib
import time
import urllib.parse
import aiohttp
import aiosqlite
import emtvlcapi
import unicodedata
import hashlib
import random
import base64
from fastapi import FastAPI, Query, HTTPException
# Patch EMT API URL to use Geoportal (which doesn't block Cloud IPs like AWS/Hetzner)
emtvlcapi.EMT_BUS_TIMES_URL = "https://geoportal.emtvalencia.es/EMT/mapfunctions/MapUtilsPetitions.php?sec=getSAE"
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional

def remove_accents(input_str):
    if not input_str:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2.0)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

app = FastAPI(title="ViaVLC API")

try:
    with open("metro_wp_mapping.json", "r") as f:
        metro_wp_mapping = json.load(f)
except FileNotFoundError:
    metro_wp_mapping = {}
    print("WARNING: metro_wp_mapping.json not found!")

# Simple thread-safe TTL Cache
class SimpleTTLCache:
    def __init__(self, ttl_seconds=30):
        self.ttl = ttl_seconds
        self.cache = {}
        
    def get(self, key):
        if key in self.cache:
            val, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return val
            else:
                del self.cache[key]
        return None
        
    def set(self, key, value):
        self.cache[key] = (value, time.time())

eta_cache = SimpleTTLCache(ttl_seconds=30)

@app.get("/api/stops")
async def get_stops(
    min_lat: Optional[float] = None,
    max_lat: Optional[float] = None,
    min_lng: Optional[float] = None,
    max_lng: Optional[float] = None
):
    """
    Returns stops within a bounding box. 
    If no bbox is provided, returns all stops (not recommended for production, but kept for fallback).
    """
    try:
        async with aiosqlite.connect('stops.db') as db:
            if min_lat is not None and max_lat is not None and min_lng is not None and max_lng is not None:
                cursor = await db.execute(
                    '''SELECT id, type, name, lat, lng, lines 
                       FROM stops 
                       WHERE lat >= ? AND lat <= ? AND lng >= ? AND lng <= ? 
                       LIMIT 5000''',
                    (min_lat, max_lat, min_lng, max_lng)
                )
            else:
                cursor = await db.execute('SELECT id, type, name, lat, lng, lines FROM stops')
                
            rows = await cursor.fetchall()
            
            stops = []
            for row in rows:
                stops.append({
                    "id": str(row[0]),  # Keep the prefix to prevent ID collisions with EMT
                    "type": row[1],
                    "name": row[2],
                    "location": {"lat": row[3], "lng": row[4]},
                    "lines": json.loads(row[5])
                })
                
            return {"success": True, "data": stops}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/search")
async def search_stops(q: str = Query(..., min_length=2)):
    """
    Search stops by name or ID.
    """
    try:
        async with aiosqlite.connect('stops.db') as db:
            query_exact = remove_accents(q)
            query_normalized = f"%{query_exact}%"
            # Priorizar palavras exatas e colocar os Metros sempre no topo!
            cursor = await db.execute(
                '''SELECT id, type, name, lat, lng, lines 
                   FROM stops 
                   WHERE name_normalized LIKE ? OR id LIKE ? 
                   ORDER BY 
                       CASE WHEN name_normalized = ? THEN 0 ELSE 1 END,
                       type DESC
                   LIMIT 15''',
                (query_normalized, query_normalized, query_exact)
            )
            rows = await cursor.fetchall()
            
            stops = []
            for row in rows:
                stops.append({
                    "id": str(row[0]),
                    "type": row[1],
                    "name": row[2],
                    "location": {"lat": row[3], "lng": row[4]},
                    "lines": json.loads(row[5])
                })
            return {"success": True, "data": stops}
    except Exception as e:
        return {"success": False, "error": str(e)}

def nearest_neighbor_sort(stops):
    if not stops: return []
    # Find the extremum point to start (e.g., minimum lat + lng)
    unvisited = sorted(stops, key=lambda s: s['lat'] + s['lng'])
    ordered = [unvisited.pop(0)]
    
    while unvisited:
        last = ordered[-1]
        nearest_idx = 0
        min_dist = float('inf')
        for i, s in enumerate(unvisited):
            dist = math.hypot(s['lat'] - last['lat'], s['lng'] - last['lng'])
            if dist < min_dist:
                min_dist = dist
                nearest_idx = i
        ordered.append(unvisited.pop(nearest_idx))
    return ordered

@app.get("/api/line_geometry")
async def get_line_geometry(line: str, type: str = "bus", destination: str = "", stop_id: str = ""):
    """
    Offline geometry via lines.db (extracted from OSM).
    Matches the requested destination string to the closest OSM destination.
    """
    try:
        db_line_ref = line
        if type in ["metro", "tram", "tram_alicante"] and db_line_ref.startswith("L"):
            db_line_ref = db_line_ref[1:]
            
        # Auto-correct type for FGV shared stops (e.g. Benimaclet passing type=metro for Tram 4)
        if type in ["metro", "tram"]:
            if str(db_line_ref) in ["4", "6", "8", "10", "11", "12"]: # Added Alicante trams
                type = "tram"
            else:
                type = "metro"
                
        # Make sure STOPS_CACHE is loaded
        if not STOPS_CACHE:
            await build_graph()
            
        # Step 1: Exact ordered stops from line_routes matching the destination & stop_id
        ordered_stops = []
        best_stop_seq = []
        best_stop_last_name = ""
        
        async with aiosqlite.connect('stops.db') as db:
            # Check if geometry_json column exists, if so select it
            try:
                cursor = await db.execute("SELECT stops_json, geometry_json FROM line_routes WHERE line=? AND type=?", (db_line_ref, type))
            except Exception:
                cursor = await db.execute("SELECT stops_json, NULL FROM line_routes WHERE line=? AND type=?", (db_line_ref, type))
            rows = await cursor.fetchall()
            
            # Fallback for Metrobus (GTFS data uses 'L' prefix)
            if not rows and type == "metrobus" and db_line_ref:
                base_line = "".join([c for c in db_line_ref if c.isdigit()])
                cursor = await db.execute("SELECT stops_json, geometry_json FROM line_routes WHERE line LIKE ? AND type=?", (f"L{base_line}%", type))
                rows = await cursor.fetchall()
                
            if rows:
                valid_rows = []
                # Find the row that contains the originStopId in its sequence (or is geographically very close)
                if stop_id:
                    origin_lat, origin_lng = None, None
                    if str(stop_id) in STOPS_CACHE:
                        origin_lat = STOPS_CACHE[str(stop_id)]['lat']
                        origin_lng = STOPS_CACHE[str(stop_id)]['lng']
                        print(f"DEBUG: origin {stop_id} coords: {origin_lat}, {origin_lng}", flush=True)
                        
                    for row in rows:
                        seq = json.loads(row[0])
                        str_seq = [str(x) for x in seq]
                        if str(stop_id) in str_seq:
                            print(f"DEBUG: exact match", flush=True)
                            valid_rows.append(row)
                        elif origin_lat is not None and origin_lng is not None:
                            # Geographic fallback: if the stop is within 400m of any stop in the sequence
                            found_close = False
                            for sid in str_seq:
                                if sid in STOPS_CACHE:
                                    s_lat = STOPS_CACHE[sid]['lat']
                                    s_lng = STOPS_CACHE[sid]['lng']
                                    if haversine_distance(origin_lat, origin_lng, s_lat, s_lng) < 400:
                                        print(f"DEBUG: geographic match on stop {sid} ({haversine_distance(origin_lat, origin_lng, s_lat, s_lng):.1f}m away)", flush=True)
                                        found_close = True
                                        break
                            if found_close:
                                valid_rows.append(row)
                                
                    print(f"DEBUG: valid_rows length: {len(valid_rows)}", flush=True)
                
                if not valid_rows:
                    valid_rows = rows
                
                # Match destination string against the sequence to pick the correct direction and truncate if needed
                if destination:
                    best_ratio = -1
                    best_rows = []
                    import difflib
                    for row in valid_rows:
                        seq = json.loads(row[0])
                        if not seq: continue
                        
                        # Check all intermediate stops and the last stop
                        for i, sid in enumerate(seq):
                            sid = str(sid)
                            if sid in STOPS_CACHE:
                                stop_name = STOPS_CACHE[sid]['name']
                                ratio = difflib.SequenceMatcher(None, destination.lower(), stop_name.lower()).ratio()
                                if destination.lower() in stop_name.lower() or stop_name.lower() in destination.lower():
                                    ratio += 0.5
                                
                                # Bonus for matching near the terminus of the route
                                if i >= len(seq) - 3:
                                    ratio += 1.0
                                
                                # Check if the train is actually going in the right direction
                                valid_dir = True
                                if stop_id:
                                    str_seq = [str(x) for x in seq]
                                    origin_idx = -1
                                    if str(stop_id) in str_seq:
                                        origin_idx = str_seq.index(str(stop_id))
                                    elif origin_lat is not None and origin_lng is not None:
                                        # Use the geographically closest stop in this sequence as the origin
                                        min_dist = 400
                                        for idx, sid in enumerate(str_seq):
                                            if sid in STOPS_CACHE:
                                                s_lat = STOPS_CACHE[sid]['lat']
                                                s_lng = STOPS_CACHE[sid]['lng']
                                                dist = haversine_distance(origin_lat, origin_lng, s_lat, s_lng)
                                                if dist < min_dist:
                                                    min_dist = dist
                                                    origin_idx = idx
                                    
                                    if origin_idx != -1 and origin_idx > i:
                                        valid_dir = False # Destination is BEFORE origin in this sequence!
                                            
                                if not valid_dir:
                                    continue
                                
                                # If it's a strong match and in the right direction, truncate the sequence up to this stop
                                if ratio > best_ratio:
                                    best_ratio = ratio
                                    truncated_seq = seq[:i+1]
                                    best_rows = [(row, truncated_seq)]
                                elif ratio == best_ratio:
                                    truncated_seq = seq[:i+1]
                                    best_rows.append((row, truncated_seq))
                    
                    if best_rows and best_ratio > 0.4:
                        valid_rows = []
                        for r, truncated_seq in best_rows:
                            new_row = list(r)
                            new_row[0] = json.dumps(truncated_seq)
                            valid_rows.append(tuple(new_row))
                
                # Sort valid_rows by the length of the stops sequence to prefer the longest/most complete route

                valid_rows.sort(key=lambda r: len(json.loads(r[0])), reverse=True)
                
                # Assume the first valid sequence is correct if we can't narrow it down further
                best_stop_seq = json.loads(valid_rows[0][0])
                
                geom_json_from_db = None
                base_geom_json = None
                
                if type in ['metro', 'tram', 'tram_alicante']:
                    # Combine ALL geometries for this line into a single MultiLineString for the base layer
                    multi_coords = []
                    for row in rows:
                        if len(row) > 1 and row[1]:
                            geom = json.loads(row[1])
                            coords = geom.get('coordinates', [])
                            if coords:
                                if geom.get('type') == 'MultiLineString':
                                    multi_coords.extend(coords)
                                else:
                                    multi_coords.append(coords)
                    
                    if multi_coords:
                        base_geom_json = json.dumps({
                            'type': 'MultiLineString',
                            'coordinates': multi_coords
                        })
                    
                    # The active geometry is the specific row matching the destination
                    if len(valid_rows[0]) > 1 and valid_rows[0][1]:
                        geom_json_from_db = valid_rows[0][1]
                else:
                    # For buses, we only draw the active geometry (no base layer)
                    if len(valid_rows[0]) > 1 and valid_rows[0][1]:
                        geom_json_from_db = valid_rows[0][1]
                    
                # Get the name of the last stop for the difflib fallback
                best_stop_last_name = None
                if best_stop_seq:
                    last_stop_id = str(best_stop_seq[-1])
                    if last_stop_id in STOPS_CACHE:
                        best_stop_last_name = STOPS_CACHE[last_stop_id]['name']

                ordered_stops_data = []
                for sid in best_stop_seq:
                    sid = str(sid)
                    if sid in STOPS_CACHE:
                        s = STOPS_CACHE[sid]
                        ordered_stops_data.append({
                            "id": sid,
                            "name": s['name'],
                            "lat": s['lat'],
                            "lng": s['lng']
                        })
                        
                all_stops_data = []
                seen_sids = set()
                for row in rows:
                    seq = json.loads(row[0])
                    for sid in seq:
                        sid = str(sid)
                        if sid not in seen_sids and sid in STOPS_CACHE:
                            seen_sids.add(sid)
                            s = STOPS_CACHE[sid]
                            all_stops_data.append({
                                "id": sid,
                                "name": s['name'],
                                "lat": s['lat'],
                                "lng": s['lng'],
                                "type": s['type']
                            })
                        
                # If active geometry is missing (e.g. injected branches like Mas del Rosari), generate point-to-point fallback
                if not geom_json_from_db and ordered_stops_data and type in ['metro', 'tram', 'tram_alicante']:
                    fallback_coords = [[s['lng'], s['lat']] for s in ordered_stops_data]
                    geom_json_from_db = json.dumps({
                        'type': 'LineString',
                        'coordinates': fallback_coords
                    })
                        
                # If we have GTFS geometry, return it immediately!
                if geom_json_from_db or base_geom_json:
                    return {
                        "success": True, 
                        "geometry": json.loads(geom_json_from_db) if geom_json_from_db else None,
                        "base_geometry": json.loads(base_geom_json) if base_geom_json else None,
                        "ordered_stops": ordered_stops_data,
                        "all_stops": all_stops_data
                    }
                else:
                    return {"success": True, "geometry": None, "ordered_stops": ordered_stops_data, "all_stops": all_stops_data, "error": "Geometría no disponible para esta ruta"}
                    
        return {"success": False, "error": "Route not found in db"}
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

async def fetch_fgv_eta(stop_id: str, city_code: str, prefix: str):
    clean_id = stop_id.replace(prefix, "")
    
    lat = None
    lng = None
    
    # Query stops.db for lat/lng and lines of this stop
    import aiosqlite
    db_name = ""
    valid_lines = set()
    try:
        async with aiosqlite.connect('stops.db') as db:
            cursor = await db.execute("SELECT lat, lng, name, lines FROM stops WHERE id = ?", (f"{prefix}{clean_id}",))
            row = await cursor.fetchone()
            if row:
                lat, lng, db_name, lines_str = row
                if lines_str and lines_str != "[]":
                    import json
                    valid_lines = set(json.loads(lines_str))
    except Exception as e:
        print(f"Error querying DB for {stop_id}: {e}")
        
    arrivals_set = set()
    arrivals = []
    
    def add_previsiones(previsiones):
        # CRITICAL FIX: The FGV backend has a bug where requesting an invalid/offline
        # station ID will return the schedules for Luceros instead of empty!
        # If the response contains lines that we KNOW do not pass through this station,
        # the entire response is ghost data. Reject the whole thing to trigger fallback.
        has_invalid_lines = False
        if valid_lines:
            for p in previsiones:
                line_name = f"L{p.get('line')}"
                if line_name not in valid_lines:
                    has_invalid_lines = True
                    break
        
        if has_invalid_lines:
            return False
            
        for p in previsiones:
            line_name = f"L{p.get('line')}"
            for t in p.get('trains', []):
                seconds = t.get('seconds', 0)
                minutos = seconds // 60
                
                eta_str = f"{minutos} min" if minutos > 0 else "Próximo"
                
                dest = t.get('destino')
                sig = f"{line_name}_{dest}_{eta_str}"
                
                if sig not in arrivals_set:
                    arrivals_set.add(sig)
                    arrivals.append({
                        "line": line_name,
                        "destination": dest,
                        "eta": eta_str,
                        "realtime": True,
                        "_seconds": seconds
                    })
        return True

    true_fgv_id = clean_id
    connector = aiohttp.TCPConnector(ssl=False)
    last_error = None
    
    async with aiohttp.ClientSession(connector=connector) as session:
        # Try horarios-cercanos first
        if lat and lng:
            url_cercanos = f'https://www.fgv.es/fgv/app/es/api/v1/{city_code}/horarios-cercanos?latitud={lat}&longitud={lng}'
            try:
                async with session.get(url_cercanos, headers={'User-Agent': 'okhttp/4.10.0', 'Accept': 'application/json'}, timeout=10) as response:
                    text = await response.text()
                    res = json.loads(text)
                    if not isinstance(res, list): res = [res]
                    
                    if res:
                        # Find the best station by combining distance and name similarity
                        best_station = None
                        best_score = -99999
                        
                        import difflib
                        for r in res:
                            api_name = r.get('estacion', {}).get('nombre', '')
                            d = r.get('estacion', {}).get('distancia_actual', 9999)
                            
                            name_ratio = difflib.SequenceMatcher(None, db_name.lower(), api_name.lower()).ratio() if db_name else 0
                            
                            # If name matches well (>0.8), allow larger distance offsets up to 1000m
                            # Otherwise require strict distance (<150m) to avoid merging separate stations
                            score = -99999
                            if name_ratio > 0.8 and d <= 1000:
                                score = 1000 * name_ratio - d
                            elif d <= 150:
                                score = 500 - d
                                
                            if score > best_score:
                                best_score = score
                                best_station = r
                        
                        if best_station and best_score > -9999:
                            discovered_id = best_station.get('estacion', {}).get('estacion_id_FGV')
                            if discovered_id:
                                true_fgv_id = str(discovered_id)
                                
                            add_previsiones(best_station.get('previsiones', []))
            except Exception as e:
                last_error = e
                print(f"Error in cercanos: {e}")

        # Also try horarios-prevision-3 as fallback, but ONLY if we didn't already successfully hit cercanos
        # For Alicante (A), ID mapping is messy and horarios-prevision-3 returns Luceros for missing stations!
        # So skip it if we already found the station in cercanos.
        if not (city_code == "A" and lat and lng):
            url_prevision = f'https://www.fgv.es/fgv/app/es/api/v1/{city_code}/horarios-prevision-3/{true_fgv_id}'
            try:
                async with session.get(url_prevision, headers={'User-Agent': 'okhttp/4.10.0', 'Accept': 'application/json'}, timeout=10) as response:
                    text = await response.text()
                    res = json.loads(text)
                    add_previsiones(res.get('previsiones', []))
            except Exception as e:
                last_error = e
                print(f"Error in prevision: {e}")

        # Ultimate fallback: scrape WordPress admin-ajax if both APIs failed
        if not arrivals:
            wp_id = clean_id
            if city_code == "V":
                # Try to map API ID to WP ID for Metrovalencia
                if clean_id in metro_wp_mapping:
                    wp_id = metro_wp_mapping[clean_id]
                else:
                    wp_id = None
            
            if wp_id:
                wp_url = f'https://www.{"metrovalencia" if city_code == "V" else "tramalacant"}.es/wp-admin/admin-ajax.php'
                wp_data = f"action=formularios_ajax&data=action%3Dinfo-estacion%26id%3D{wp_id}"
                headers = {'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded'}
                try:
                    async with session.post(wp_url, data=wp_data, headers=headers, timeout=5) as response:
                        text = await response.text()
                        try:
                            res_json = json.loads(text)
                            html = res_json.get('html', '')
                        except:
                            html = ''
                            
                        if html:
                            import re
                            blocks = html.split('item--proximos')[1:]
                            for block in blocks:
                                line_match = re.search(r'linea-(\d+)', block)
                                dest_match = re.search(r'<div class="nombre-estacion">([^<]+)</div>', block)
                                time_match = re.search(r'<span class="minutos[^>]*>([^<]+)</span>', block)
                                
                                if dest_match and time_match:
                                    line_name = f"L{line_match.group(1)}" if line_match else "Tram"
                                    dest = dest_match.group(1).strip()
                                    eta_str = time_match.group(1).strip()
                                    
                                    seconds = 9999
                                    if 'min' in eta_str.lower():
                                        try:
                                            m = int(re.search(r'\d+', eta_str).group(0))
                                            seconds = m * 60
                                        except:
                                            pass
                                    elif 'próx' in eta_str.lower() or 'prox' in eta_str.lower():
                                        seconds = 0
                                            
                                    sig = f"{line_name}_{dest}_{eta_str}"
                                    if sig not in arrivals_set:
                                        arrivals_set.add(sig)
                                        arrivals.append({
                                            "line": line_name,
                                            "destination": dest,
                                            "eta": eta_str,
                                            "realtime": False,
                                            "_seconds": seconds
                                        })
                                    
                except Exception as e:
                    last_error = e
                    print(f"Error in WP fallback: {e}")

    if not arrivals and last_error:
        raise last_error

    # Sort by ETA
    arrivals.sort(key=lambda x: x.get('seconds', 9999))
    
    # Remove seconds key before returning
    for a in arrivals:
        a.pop('seconds', None)
        
    return arrivals

async def fetch_metro_eta(stop_id: str):
    return await fetch_fgv_eta(stop_id, "V", "metro-")

async def fetch_tram_eta(stop_id: str):
    return await fetch_fgv_eta(stop_id, "A", "tram-")

def get_emt_wsse_header():
    user_key = "7gH8m45w7A"
    password = "b0cb3f0957ab095e17fec2656528d46eb78d53a7cd21cc8a9e5608d125377732"
    
    rand_long = random.randint(-9223372036854775808, 9223372036854775807)
    calculateMD5 = hashlib.md5(str(rand_long).encode('utf-8')).hexdigest()
    l = str(int(time.time()))
    sha1_input = calculateMD5 + l + password.lower()
    encode = hashlib.sha1(sha1_input.encode('utf-8')).hexdigest()
    
    b64_encode = base64.b64encode(encode.encode('utf-8')).decode('utf-8')
    b64_md5 = base64.b64encode(calculateMD5.encode('utf-8')).decode('utf-8')
    
    return 'UsernameToken Username="%s", PasswordDigest="%s", Nonce="%s", Created="%s"' % (user_key, b64_encode, b64_md5, l)

def fetch_bus_eta_sync(stop_id: str):
    arrivals = []
    url = f"https://servicios.emtvalencia.es/estimaciones/estimacion.php?idioma=es&parada={stop_id}&adaptados=false&getNBus=1"
    
    headers = {
        'User-Agent': 'EMT-Valencia/7.32 (Android 11)',
        'x-wsse': get_emt_wsse_header()
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    import xml.etree.ElementTree as ET
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        xml_data = resp.read().decode('utf-8', errors='ignore')
        root = ET.fromstring(xml_data)
        
        # XML structure looks like: <estimacion parada="383"><solo_parada><bus><linea>62</linea><destino>Benimàmet</destino><minutos>8 min.</minutos></bus>...
        for bus in root.findall('.//bus'):
            line_el = bus.find('linea')
            mins_el = bus.find('minutos')
            dest_el = bus.find('destino')
            
            line = line_el.text if line_el is not None else ''
            mins = mins_el.text if mins_el is not None else '?'
            dest = dest_el.text if dest_el is not None else ''
            
            # Clean up destination
            dest = str(dest).replace('<![CDATA[', '').replace(']]>', '')
            
            # Clean up minutes (e.g. "30 min.", "Próximo")
            if "min" in mins.lower():
                mins = mins.lower().replace("min.", "").replace("min", "").strip()
                
            eta_val = "Próximo" if mins == "0" else f"{mins} min" if mins.isdigit() else mins
            arrivals.append({
                "line": str(line),
                "eta": eta_val,
                "destination": str(dest)
            })
            
    except Exception as e:
        print(f"Error fetching EMT ETAs for stop {stop_id}: {e}")
        raise e
        
    def _sort_eta(x):
        val = x.get('eta', '')
        if "próx" in val.lower() or "prox" in val.lower(): return 0
        import re
        nums = re.findall(r'\d+', val)
        return int(nums[0]) if nums else 999
        
    arrivals.sort(key=_sort_eta)
    return arrivals

async def fetch_metrobus_eta(stop_id: str):
    """
    Fetch ETA from Metrobus Softour API. If real-time is empty, fallback to schedules!
    """
    try:
        if stop_id.startswith('gtfs-'):
            return []
            
        # Strip the prefix added to prevent DB collision with EMT
        actual_id = stop_id.replace("metrobus-", "") if stop_id.startswith("metrobus-") else stop_id
        
        # 1. Try Real-Time API
        url = f"https://api.softoursistemas.com/metrobus/estimacion/ocupacion/{actual_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=8)
        data = json.loads(resp.read().decode('utf-8'))
        
        arrivals = []
        is_realtime = True
        
        if data:
            for est in data:
                line = str(est.get('line', ''))
                dest = str(est.get('route', ''))
                
                estimations = est.get('estimations', [])
                if estimations:
                    for arrival_est in estimations:
                        mins = str(arrival_est.get('minutesToArrival', '0'))
                        if mins.isdigit():
                            mins += " min"
                        
                        arrivals.append({
                            "line": line,
                            "eta": mins,
                            "destination": dest,
                            "realtime": True
                        })
        else:
            # 2. Fallback to Scheduled API
            from datetime import datetime, timedelta, timezone
            # Valencia is UTC+1 (Winter) or UTC+2 (Summer). 
            # We'll approximate with UTC+2 for now, or just use system local time if it's correct.
            now = datetime.now(timezone.utc) + timedelta(hours=2) 
            date_str = now.strftime('%Y%m%d')
            
            sched_url = f"https://api.softoursistemas.com/metrobus/stops/code/{actual_id}/times?date={date_str}"
            s_req = urllib.request.Request(sched_url, headers={'User-Agent': 'Mozilla/5.0'})
            s_resp = await asyncio.to_thread(urllib.request.urlopen, s_req, timeout=8)
            s_data = json.loads(s_resp.read().decode('utf-8'))
            
            is_realtime = False
            current_hour = now.hour
            current_minute = now.minute
            
            schedules = []
            for h_str, items in s_data.items():
                h = int(h_str)
                for item in items:
                    m = int(item.get('minute', 0))
                    # Add all schedules
                    schedules.append((item.get('route_short_name', ''), item.get('direction', ''), h, m))
            
            # Remove duplicate API entries
            unique_schedules = []
            seen = set()
            for s in schedules:
                if s not in seen:
                    seen.add(s)
                    unique_schedules.append(s)
            schedules = unique_schedules
            
            # Sort by time relative to current time
            # If a schedule is earlier in the day than current time, we assume it's for tomorrow
            schedules.sort(key=lambda x: (x[2]*60 + x[3]) if (x[2]*60 + x[3]) >= (current_hour*60 + current_minute) else ((x[2]+24)*60 + x[3]))
            
            # Take only the next 6 schedules to avoid cluttering
            for s in schedules[:6]:
                h, m = s[2], s[3]
                wait_mins = (h - current_hour) * 60 + (m - current_minute)
                if wait_mins < 0: wait_mins += 24*60
                
                # If it's more than 24h (which shouldn't happen), skip
                if wait_mins >= 24*60: continue
                
                arrivals.append({
                    "line": str(s[0]),
                    "eta": f"{wait_mins} min",
                    "destination": str(s[1]),
                    "realtime": False
                })

        return arrivals
        
    except Exception as e:
        print(f"Error fetching Metrobus ETA for {stop_id}: {e}")
        return []

from fastapi import Response

@app.get("/api/eta")
async def get_eta(id: str, type: str, response: Response = None):
    # Set cache headers for the client
    if response:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Expires"] = "0"
        response.headers["Pragma"] = "no-cache"
    
    if not id or not type:
        raise HTTPException(status_code=400, detail="Missing parameters")
        
    cache_key = f"{type}-{id}"
    cached_data = eta_cache.get(cache_key)
    if cached_data is not None:
        return {"success": True, "data": cached_data, "cached": True}
        
    arrivals = []
    
    try:
        if type == "bus":
            arrivals = await asyncio.to_thread(fetch_bus_eta_sync, id)
        elif type == "metro":
            arrivals = await fetch_metro_eta(id)
        elif type == "tram":
            arrivals = await fetch_fgv_eta(id, "A", "tram-")
        elif type == "tram_alicante":
            arrivals = await fetch_fgv_eta(id, "A", "tram_alicante-")
        elif type == "metrobus":
            arrivals = await fetch_metrobus_eta(id)
        else:
            return {"success": False, "error": "Unknown transport type"}
        
        # Update cache
        eta_cache.set(cache_key, arrivals)
        return {"success": True, "data": arrivals, "cached": False}
        
    except Exception as e:
        print(f"Error fetching ETA for {type} {id}: {e}")
        # Return timeout True so get_journey applies fallback instead of dropping the route
        return {"success": False, "data": [], "timeout": True}

STOPS_CACHE = {}
TRANSIT_GRAPH = {}

def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

@app.on_event("startup")
async def build_graph():
    global STOPS_CACHE, TRANSIT_GRAPH
    print("Building multi-modal transit graph...")
    
    SPEEDS = {'metro': 25000/60, 'tram': 20000/60, 'bus': 15000/60, 'metrobus': 15000/60}
    WALK_SPEED = 66.66 # meters per minute
    TRANSFER_PENALTY = 5 # minutes
    
    try:
        async with aiosqlite.connect('stops.db') as db:
            cursor = await db.execute("SELECT id, lat, lng, type, name FROM stops")
            rows = await cursor.fetchall()
            STOPS_CACHE = {r[0]: {'lat': r[1], 'lng': r[2], 'type': r[3], 'name': r[4]} for r in rows}
            
        TRANSIT_GRAPH = {sid: [] for sid in STOPS_CACHE.keys()}
        
        async with aiosqlite.connect('lines.db') as db:
            cursor = await db.execute("SELECT route_id, ref, stops_json, type FROM route_stops JOIN routes ON routes.id = route_stops.route_id")
            for rid, ref, stops_json, rtype in await cursor.fetchall():
                if rtype == 'bus':
                    continue # Ignore incomplete OSM bus routes!
                    
                seq = json.loads(stops_json)
                for i in range(len(seq) - 1):
                    s1, s2 = seq[i], seq[i+1]
                    sid1, sid2 = s1['id'], s2['id']
                    if sid1 not in STOPS_CACHE or sid2 not in STOPS_CACHE: continue
                    
                    dist = s2['dist_along'] - s1['dist_along']
                    if dist <= 0: dist = 100
                    
                    stype = STOPS_CACHE[sid1]['type']
                    weight = dist / SPEEDS.get(stype, 15000/60) + 0.5 # 30s stop penalty
                    
                    TRANSIT_GRAPH[sid1].append((sid2, weight, 'transit', ref, stype, rid))
                    
        # Load GTFS Bus routes (Bi-directional)
        async with aiosqlite.connect('stops.db') as db:
            cursor = await db.execute("SELECT line, direction, stops_json, type FROM line_routes")
            for ref, direction, stops_json, stype in await cursor.fetchall():
                seq = json.loads(stops_json)
                rid = f"gtfs_{ref}_{direction}"
                for i in range(len(seq) - 1):
                    sid1, sid2 = str(seq[i]), str(seq[i+1])
                    if sid1 not in STOPS_CACHE or sid2 not in STOPS_CACHE: continue
                    
                    dist = calculate_haversine(STOPS_CACHE[sid1]['lat'], STOPS_CACHE[sid1]['lng'], STOPS_CACHE[sid2]['lat'], STOPS_CACHE[sid2]['lng'])
                    if dist <= 0: dist = 100
                    
                    weight = dist / SPEEDS.get(stype, 15000/60) + 0.5 # 30s stop penalty
                    TRANSIT_GRAPH[sid1].append((sid2, weight, 'transit', str(ref), stype, rid))
                    
        # Build spatial grid for O(N) transfer edge generation
        grid = {}
        grid_size = 0.003 # approx 300m
        for sid, data in STOPS_CACHE.items():
            bx = int(data['lat'] / grid_size)
            by = int(data['lng'] / grid_size)
            if (bx, by) not in grid: grid[(bx, by)] = []
            grid[(bx, by)].append(sid)
            
        for (bx, by), bucket_sids in grid.items():
            # Gather sids from this bucket and adjacent buckets
            compare_sids = []
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    compare_sids.extend(grid.get((bx + dx, by + dy), []))
                    
            for sid1 in bucket_sids:
                for sid2 in compare_sids:
                    if sid1 >= sid2: continue # Avoid duplicate pairs
                    d = calculate_haversine(STOPS_CACHE[sid1]['lat'], STOPS_CACHE[sid1]['lng'], STOPS_CACHE[sid2]['lat'], STOPS_CACHE[sid2]['lng'])
                    if d <= 300: # 300m transfer distance
                        weight = (d / WALK_SPEED) + TRANSFER_PENALTY
                        TRANSIT_GRAPH[sid1].append((sid2, weight, 'transfer', 'walk', 'walk', None))
                        TRANSIT_GRAPH[sid2].append((sid1, weight, 'transfer', 'walk', 'walk', None))
                        
        print("Graph built successfully.")
    except Exception as e:
        print(f"Error building graph: {e}")

def parse_time_str(time_str):
    if not time_str: return float('inf')
    if isinstance(time_str, (int, float)): return time_str
    if ":" in str(time_str):
        parts = str(time_str).split(":")
        if len(parts) == 3: return int(parts[0])*60 + int(parts[1])
        if len(parts) == 2: return int(parts[0])
    match = re.search(r'(\d+)', str(time_str))
    if match: return int(match.group(1))
    return float('inf')

async def fetch_osrm_walk(lat1, lon1, lat2, lon2):
    try:
        url = f"http://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false"
        req = urllib.request.Request(url, headers={'User-Agent': 'ViaVLC/1.0'})
        resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=2)
        data = json.loads(resp.read().decode('utf-8'))
        if data.get('code') == 'Ok' and data.get('routes'):
            r = data['routes'][0]
            # OSRM returns duration in seconds, distance in meters
            return r['duration'] / 60.0, r['distance']
    except Exception as e:
        print(f"OSRM Error: {e}")
        pass
    # Fallback to straight-line haversine * 1.4 detour factor
    dist = calculate_haversine(lat1, lon1, lat2, lon2) * 1.4
    return dist / (4.0 * 1000 / 60), dist

@app.get("/api/journey")
async def get_journey(orig_lat: float, orig_lng: float, dest_lat: float, dest_lng: float):
    from datetime import datetime, timedelta
    
    # Get current local time
    now = datetime.now()
    
    # CRITICAL HACK: We subtract 15 minutes from the current time to feed into OTP.
    # Why? If a train is delayed (e.g. theoretical departure at 23:49, but real arrival at 23:55),
    # OTP at 23:50 will NOT suggest it because GTFS says it already left.
    # By asking OTP for routes from 15 minutes ago, it suggests the 23:49 train.
    # Our real-time interceptor then asks the API: "Is this train still coming?".
    # If it is delayed, the API says "Yes, in 16 mins", and we validate the route!
    # If it truly left, the API says "No", and we prune it.
    past_time = now - timedelta(minutes=15)
    current_time_str = past_time.strftime('%I:%M%p').lower()
    
    OTP_URL = "http://localhost:8080/otp/routers/default/plan"
    params = {
        "fromPlace": f"{orig_lat},{orig_lng}",
        "toPlace": f"{dest_lat},{dest_lng}",
        "time": current_time_str,
        "date": "06-30-2026",
        "mode": "TRANSIT,WALK",
        "maxWalkDistance": 2000,
        "arriveBy": "false",
        "numItineraries": 10
    }
    
    import httpx
    import math
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(OTP_URL, params=params, timeout=15.0)
            
        if response.status_code != 200:
            return {"success": False, "error": "Error connecting to OTP"}
            
        data = response.json()
        
        if "error" in data:
            return {"success": False, "error": data["error"].get("msg", "Routing error")}
            
        if not data.get("plan", {}).get("itineraries"):
            return {"success": True, "routes": []} # No routes found
            
        # We will process all itineraries and keep the valid ones
        import asyncio
        valid_routes = []
        
        async def get_live_eta_for_leg(leg):
            if leg["mode"] == "WALK": return None
            
            agency_lower = leg.get("agencyName", "").lower()
            leg_type = "bus"
            if "metrobus" in agency_lower: leg_type = "metrobus"
            elif "metro valencia" in agency_lower or "metrovalencia" in agency_lower: leg_type = "metro"
            elif "tram" in agency_lower: leg_type = "tram"
            
            stop_id = leg["from"].get("stopId", "")
            if ":" in stop_id: stop_id = stop_id.split(":")[-1]
            
            # CRITICAL FIX: GTFS stop IDs for Metro/Tram (e.g. 41) DO NOT match the internal FGV real-time API IDs (e.g. 57)
            # We must map them by finding the geographically closest stop in our stops.db, just like the frontend popup does.
            if leg_type in ["metro", "tram"]:
                lat = leg["from"]["lat"]
                lng = leg["from"]["lon"]
                
                import aiosqlite
                best_id = None
                best_dist = 999999
                try:
                    async with aiosqlite.connect('stops.db') as db:
                        cursor = await db.execute('SELECT id, lat, lng FROM stops WHERE type = ?', (leg_type,))
                        rows = await cursor.fetchall()
                        for r_id, r_lat, r_lng in rows:
                            d = calculate_haversine(lat, lng, r_lat, r_lng)
                            if d < best_dist:
                                best_dist = d
                                best_id = r_id
                except Exception as e:
                    pass
                    
                if best_id and best_dist < 100: # Must be within 100 meters
                    if "-" in best_id:
                        stop_id = best_id.split("-")[-1]
                    else:
                        stop_id = best_id
                        
            if not stop_id: return None
            return await get_eta(stop_id, leg_type)

        for itinerary in data["plan"]["itineraries"]:
            clean_response = {
                "duration_minutes": math.ceil(itinerary["duration"] / 60),
                "walk_distance_meters": round(itinerary["walkDistance"]),
                "transfers": itinerary["transfers"],
                "legs": []
            }
            
            # Fetch ETAs for all legs in this itinerary concurrently
            tasks = [get_live_eta_for_leg(leg) for leg in itinerary["legs"]]
            eta_results = await asyncio.gather(*tasks)
            
            is_valid_route = True
            accumulated_time = 0
            
            for leg, eta_resp in zip(itinerary["legs"], eta_results):
                clean_leg = {
                    "mode": leg["mode"],
                    "start_name": leg["from"]["name"],
                    "start_lat": leg["from"]["lat"],
                    "start_lon": leg["from"]["lon"],
                    "start_id": leg["from"].get("stopId"),
                    "end_name": leg["to"]["name"],
                    "end_lat": leg["to"]["lat"],
                    "end_lon": leg["to"]["lon"],
                    "end_id": leg["to"].get("stopId"),
                    "distance": round(leg["distance"]),
                    "duration_minutes": math.ceil(leg["duration"] / 60),
                    "polyline": leg["legGeometry"]["points"]
                }
                
                if leg["mode"] == "WALK":
                    accumulated_time += clean_leg["duration_minutes"]
                
                if leg["mode"] != "WALK":
                    clean_leg["route"] = leg.get("route", "")
                    clean_leg["routeShortName"] = leg.get("routeShortName", "")
                    clean_leg["agency"] = leg.get("agencyName", "")
                    clean_leg["color"] = leg.get("routeColor", "")
                    clean_leg["headsign"] = leg.get("headsign", "")
                    clean_leg["stops"] = [
                        {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"], "id": stop.get("stopId")}
                        for stop in leg.get("intermediateStops", [])
                    ]
                    
                    # Match with real-time ETA
                    line_display = leg.get("routeShortName") or leg.get("route", "")
                    leg_type = "bus"
                    agency_lower = clean_leg["agency"].lower()
                    if "metrobus" in agency_lower: leg_type = "metrobus"
                    elif "metro valencia" in agency_lower or "metrovalencia" in agency_lower: leg_type = "metro"
                    elif "tram" in agency_lower: leg_type = "tram"
                    
                    if line_display and not line_display.startswith("L") and leg_type in ["metro", "tram"]:
                        line_display = "L" + line_display
                        
                    has_live = False
                    
                    if eta_resp and eta_resp.get("success"):
                        for arrival in eta_resp.get("data", []):
                            arr_line = str(arrival.get("line", "")).strip()
                            # Loose match: "4" == "L4" or "4" == "4"
                            if arr_line == str(leg.get("routeShortName", "")) or arr_line == line_display:
                                arr_eta = arrival.get("eta", "")
                                
                                # Verify if the user can physically reach the station in time
                                can_reach = True
                                eta_mins = 0
                                if "próx" in arr_eta.lower() or "prox" in arr_eta.lower():
                                    eta_mins = 0
                                else:
                                    import re
                                    nums = re.findall(r'\d+', arr_eta)
                                    if nums:
                                        eta_mins = int(nums[0])
                                        
                                if eta_mins < (accumulated_time - 5):
                                    can_reach = False
                                    
                                if not can_reach:
                                    # Cannot reach this specific vehicle in time. Skip to see if there's a later one.
                                    continue
                                
                                clean_leg["live_eta"] = arr_eta
                                has_live = True
                                
                                # Re-calculate accumulated time with the real wait time
                                accumulated_time = eta_mins + clean_leg["duration_minutes"]
                                break
                                
                    if not has_live:
                        # If ETA api was successful but didn't contain our vehicle (or was completely empty), the route is dead right now.
                        if eta_resp and eta_resp.get("success") and not eta_resp.get("timeout"):
                            is_valid_route = False
                            break
                            
                clean_response["legs"].append(clean_leg)
                
            if is_valid_route:
                # Update total duration to perfectly match the real-time flow
                clean_response["duration_minutes"] = accumulated_time
                valid_routes.append(clean_response)
                
        if not valid_routes:
            return {"success": False, "error": "Ruta inválida: los transportes sugeridos no están en circulación activa en este momento."}
            
        # Categorize routes to guarantee diversity (1 Rail, 1 Bus, 1 Walk)
        walk_routes = []
        bus_routes = []
        rail_routes = []
        
        for r in valid_routes:
            is_rail = False
            is_bus = False
            
            for leg in r["legs"]:
                if leg["mode"] != "WALK":
                    agency_lower = leg.get("agency", "").lower()
                    if "metro valencia" in agency_lower or "metrovalencia" in agency_lower or "tram" in agency_lower:
                        is_rail = True
                    else:
                        is_bus = True
            
            if is_rail:
                rail_routes.append(r)
            elif is_bus:
                bus_routes.append(r)
            else:
                walk_routes.append(r)
                
        # Sort each bucket by true real-time duration
        walk_routes.sort(key=lambda r: r["duration_minutes"])
        bus_routes.sort(key=lambda r: r["duration_minutes"])
        rail_routes.sort(key=lambda r: r["duration_minutes"])
        
        # Pick the top 1 from each category
        final_routes = []
        if rail_routes: final_routes.append(rail_routes[0])
        if bus_routes: final_routes.append(bus_routes[0])
        if walk_routes: final_routes.append(walk_routes[0])
        
        # Sort the final combination so the absolute fastest appears first
        final_routes.sort(key=lambda r: r["duration_minutes"])
        
        return {"success": True, "routes": final_routes}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

# Serve static files
app.mount("/css", StaticFiles(directory="static/css"), name="css")
app.mount("/js", StaticFiles(directory="static/js"), name="js")
app.mount("/images", StaticFiles(directory="static/images"), name="images")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse("static/manifest.json")

@app.get("/sw.js")
async def serve_sw():
    return FileResponse("static/sw.js")

@app.get("/favicon.png")
async def serve_favicon():
    return FileResponse("static/favicon.png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)
