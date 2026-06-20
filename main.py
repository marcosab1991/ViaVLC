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
async def get_line_geometry(line: str, type: str = "bus", destination: str = ""):
    """
    Offline geometry via lines.db (extracted from OSM).
    Matches the requested destination string to the closest OSM destination.
    """
    try:
        # Step 1: Find best matching geometry from lines.db
        geometry = None
        
        db_line_ref = line
        if type == "metro" and db_line_ref.startswith("L"):
            db_line_ref = db_line_ref[1:]
            
        async with aiosqlite.connect('lines.db') as db:
            cursor = await db.execute('SELECT destination, geometry_json FROM routes WHERE ref=? AND type=?', (db_line_ref, type))
            rows = await cursor.fetchall()
            
            # Fallback for Metrobus: API often returns base number (e.g. 145) but DB has variants (145A, 145B)
            if not rows and type == "metrobus" and db_line_ref:
                base_ref = "".join([c for c in db_line_ref if c.isdigit()])
                cursor = await db.execute('SELECT destination, geometry_json FROM routes WHERE ref LIKE ? AND type=?', (f"{base_ref}%", type))
                rows = await cursor.fetchall()
                
            if rows:
                if destination:
                    best_geom = None
                    highest_ratio = -1
                    target_dest = remove_accents(destination.lower())
                    
                    for row_dest, geom_json in rows:
                        db_dest = remove_accents((row_dest or "").lower())
                        ratio = difflib.SequenceMatcher(None, db_dest, target_dest).ratio()
                        if ratio > highest_ratio:
                            highest_ratio = ratio
                            best_geom = geom_json
                    
                    if best_geom:
                        geometry = json.loads(best_geom)
                else:
                    # Merge all geometries for this line to show all branches
                    merged_coords = []
                    for row_dest, geom_json in rows:
                        try:
                            geom = json.loads(geom_json)
                            if geom.get("type") == "MultiLineString":
                                merged_coords.extend(geom.get("coordinates", []))
                            elif geom.get("type") == "LineString":
                                merged_coords.append(geom.get("coordinates", []))
                        except Exception:
                            pass
                    
                    if merged_coords:
                        geometry = {
                            "type": "MultiLineString",
                            "coordinates": merged_coords
                        }
        
        # Step 2: Fallback ordered stops (if needed by frontend to draw points)
        async with aiosqlite.connect('stops.db') as db:
            search_line = line
            if type in ['metro', 'tram'] and not search_line.startswith('L'):
                search_line = f"L{search_line}"
                
            cursor = await db.execute(
                'SELECT id, name, lat, lng FROM stops WHERE type=? AND lines LIKE ? LIMIT 150',
                (type, f'%"{search_line}"%')
            )
            rows = await cursor.fetchall()
            
            # Fallback for Metrobus stops: API often returns base number (e.g. 145) but DB has variants (145A)
            if not rows and type == "metrobus" and line:
                base_line = "".join([c for c in line if c.isdigit()])
                cursor = await db.execute(
                    'SELECT id, name, lat, lng FROM stops WHERE type=? AND lines LIKE ? LIMIT 150',
                    (type, f'%"{base_line}%"%')
                )
                rows = await cursor.fetchall()
                
            raw_stops = []
            for row in rows:
                raw_stops.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "lat": row[2],
                    "lng": row[3]
                })
            
            # Since we have the exact geometry from OSM, we don't strictly need to order stops perfectly 
            # for drawing the line anymore. But we return them ordered by Nearest Neighbor for the markers.
            ordered_stops = nearest_neighbor_sort(raw_stops)

        return {
            "success": True, 
            "geometry": geometry,
            "ordered_stops": ordered_stops
        }
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

async def fetch_fgv_eta(stop_id: str, city_code: str, prefix: str):
    clean_id = stop_id.replace(prefix, "")
    
    lat = None
    lng = None
    
    # Query stops.db for lat/lng of this stop
    import aiosqlite
    try:
        async with aiosqlite.connect('stops.db') as db:
            cursor = await db.execute("SELECT lat, lng FROM stops WHERE id = ?", (f"{prefix}{clean_id}",))
            row = await cursor.fetchone()
            if row:
                lat, lng = row
    except Exception as e:
        print(f"Error querying lat/lng for {stop_id}: {e}")
        
    arrivals_set = set()
    arrivals = []
    
    def add_previsiones(previsiones):
        for p in previsiones:
            line_name = f"L{p.get('line')}"
            for t in p.get('trains', []):
                seconds = t.get('seconds', 0)
                minutos = seconds // 60
                eta_str = "Próximo" if minutos == 0 else f"{minutos} min"
                dest = t.get('destino')
                
                # Deduplicate based on line, destination, and roughly the same time
                sig = f"{line_name}_{dest}_{minutos}"
                if sig not in arrivals_set:
                    arrivals_set.add(sig)
                    arrivals.append({
                        "line": line_name,
                        "destination": dest,
                        "eta": eta_str,
                        "seconds": seconds
                    })

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
                        # Find the closest station to avoid merging nearby stations
                        closest_station = min(res, key=lambda x: x.get('estacion', {}).get('distancia_actual', 9999))
                        dist = closest_station.get('estacion', {}).get('distancia_actual', 9999)
                        
                        # Dynamically discover the true FGV API ID!
                        discovered_id = closest_station.get('estacion', {}).get('estacion_id_FGV')
                        if discovered_id:
                            true_fgv_id = str(discovered_id)
                            
                        if dist <= 150:
                            add_previsiones(closest_station.get('previsiones', []))
            except Exception as e:
                last_error = e
                print(f"Error in cercanos: {e}")

        # Also try horarios-prevision-3 as fallback
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
            arrivals = await fetch_tram_eta(id)
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
    if not STOPS_CACHE or not TRANSIT_GRAPH:
        return {"success": False, "error": "Graph not initialized"}
        
    import heapq
    WALK_SPEED = 66.66 # meters per min
    
    orig_stops = []
    for sid, sdata in STOPS_CACHE.items():
        d = calculate_haversine(orig_lat, orig_lng, sdata['lat'], sdata['lng']) * 1.4
        if d <= 600 * 1.4:
            orig_stops.append((sid, d / WALK_SPEED))
            
    dest_stops = {}
    for sid, sdata in STOPS_CACHE.items():
        d = calculate_haversine(dest_lat, dest_lng, sdata['lat'], sdata['lng']) * 1.4
        if d <= 600 * 1.4:
            dest_stops[sid] = d / WALK_SPEED
            
    disabled_lines = set()
    best_overall_route = None
    
    for retry in range(10):
        pq = []
        for sid, w in orig_stops:
            heapq.heappush(pq, (w, sid, [(sid, w, 'walk', 'walk', 'walk', None)]))
            
        visited = {}
        best_path = None
        best_weight = float('inf')
        
        while pq:
            curr_w, u, path = heapq.heappop(pq)
            if curr_w >= best_weight: continue
            if u in visited and visited[u] <= curr_w: continue
            visited[u] = curr_w
            
            if u in dest_stops:
                final_w = curr_w + dest_stops[u]
                if final_w < best_weight:
                    best_weight = final_w
                    best_path = path + [('DEST', dest_stops[u], 'walk', 'walk', 'walk', None)]
                    
            for v, w_edge, edge_type, ref, ttype, rid in TRANSIT_GRAPH.get(u, []):
                if edge_type == 'transit' and (ref, ttype) in disabled_lines:
                    continue
                
                new_w = curr_w + w_edge
                if edge_type == 'transit' and path:
                    prev_edge_type = path[-1][2]
                    prev_ref = path[-1][3]
                    if prev_edge_type == 'transit' and prev_ref != ref:
                        new_w += 5.0 # 5 min penalty
                        
                if new_w < best_weight:
                    heapq.heappush(pq, (new_w, v, path + [(v, w_edge, edge_type, ref, ttype, rid)]))
                    
        if not best_path:
            break
            
        legs = []
        first_stop = best_path[0][0]
        legs.append({
            'type': 'walk',
            'time': round(best_path[0][1]),
            'orig_lat': orig_lat, 'orig_lng': orig_lng,
            'dest_stop': STOPS_CACHE[first_stop]['name'],
            'stops_coords': [[orig_lat, orig_lng], [STOPS_CACHE[first_stop]['lat'], STOPS_CACHE[first_stop]['lng']]]
        })
        current_leg = None
        route_ids_used = set()
        
        for i in range(1, len(best_path)):
            node, w_edge, edge_type, ref, ttype, rid = best_path[i]
            prev_node = best_path[i-1][0]
            
            if node == 'DEST':
                if current_leg: legs.append(current_leg)
                legs.append({
                    'type': 'walk',
                    'time': round(w_edge),
                    'orig_stop': STOPS_CACHE[prev_node]['name'],
                    'dest_lat': dest_lat, 'dest_lng': dest_lng,
                    'stops_coords': [[STOPS_CACHE[prev_node]['lat'], STOPS_CACHE[prev_node]['lng']], [dest_lat, dest_lng]]
                })
                break
                
            if edge_type == 'walk' or edge_type == 'transfer':
                if current_leg: 
                    legs.append(current_leg)
                    current_leg = None
                legs.append({
                    'type': 'walk',
                    'time': round(w_edge),
                    'orig_stop': STOPS_CACHE[prev_node]['name'],
                    'dest_stop': STOPS_CACHE[node]['name'],
                    'stops_coords': [[STOPS_CACHE[prev_node]['lat'], STOPS_CACHE[prev_node]['lng']], [STOPS_CACHE[node]['lat'], STOPS_CACHE[node]['lng']]]
                })
            else:
                if rid: route_ids_used.add(rid)
                if not current_leg or current_leg['line'] != ref:
                    if current_leg: legs.append(current_leg)
                    current_leg = {
                        'type': ttype,
                        'line': ref,
                        'orig_id': prev_node,
                        'orig_stop': STOPS_CACHE[prev_node]['name'],
                        'dest_stop': STOPS_CACHE[node]['name'],
                        'time': w_edge,
                        'stops_coords': [
                            [STOPS_CACHE[prev_node]['lat'], STOPS_CACHE[prev_node]['lng']],
                            [STOPS_CACHE[node]['lat'], STOPS_CACHE[node]['lng']]
                        ],
                        'stops_names': [
                            STOPS_CACHE[prev_node]['name'],
                            STOPS_CACHE[node]['name']
                        ]
                    }
                else:
                    current_leg['dest_stop'] = STOPS_CACHE[node]['name']
                    current_leg['time'] += w_edge
                    current_leg['stops_coords'].append([STOPS_CACHE[node]['lat'], STOPS_CACHE[node]['lng']])
                    current_leg['stops_names'].append(STOPS_CACHE[node]['name'])
                    
        for leg in legs:
            if 'time' in leg:
                leg['time'] = round(leg['time'])
                
        # Fetch ETA for all transit legs concurrently
        transit_legs = [(i, l) for i, l in enumerate(legs) if l['type'] not in ['walk']]
        pruned_any = False
        
        async def fetch_raw_etas(leg):
            try:
                eta_res = await asyncio.wait_for(get_eta(leg['orig_id'], leg['type']), timeout=10.0)
                if eta_res.get('success'):
                    def match_line(api_line, graph_line):
                        a = str(api_line).lstrip('L').lower()
                        b = str(graph_line).lstrip('L').lower()
                        return a == b
                        
                    etas = [e for e in eta_res['data'] if match_line(e.get('line'), leg['line'])]
                    return etas
                return None
            except Exception:
                return None

        if transit_legs:
            raw_eta_results = await asyncio.gather(*(fetch_raw_etas(l) for _, l in transit_legs))
            eta_map = {l['orig_id']: res for (_, l), res in zip(transit_legs, raw_eta_results)}
            
            cumulative_time = 0
            for leg in legs:
                if leg['type'] == 'walk':
                    cumulative_time += leg['time']
                else:
                    etas = eta_map.get(leg['orig_id'])
                    
                    if etas is None:
                        # API explicitly failed (e.g. Timeout/Cloudflare). Fallback to 0 wait.
                        leg['wait_time'] = 0
                        leg['live_eta'] = "N/A"
                        cumulative_time += leg['time']
                        continue
                        
                    if not etas:
                        # API succeeded but line has no ETAs -> Line is dead!
                        disabled_lines.add((leg['line'], leg['type']))
                        pruned_any = True
                        break
                        
                    # Find first bus/train that departs AFTER we arrive at the stop
                    best_wait = float('inf')
                    best_eta = None
                    
                    for e in etas:
                        eta_mins = parse_time_str(e.get('eta'))
                        if eta_mins == float('inf'): continue
                        
                        # Can we catch this vehicle?
                        if eta_mins >= cumulative_time:
                            wait = eta_mins - cumulative_time
                            if wait < best_wait:
                                best_wait = wait
                                best_eta = e.get('eta')
                                
                    if best_eta is None:
                        # We missed all listed vehicles (e.g. they arrive before we get there)
                        # We shouldn't prune the route because more will come later.
                        # Fallback to an average wait (10 mins)
                        best_wait = 10
                        best_eta = "N/A"
                        
                    if best_wait > 90:
                        # Wait time is absurd, consider it dead
                        disabled_lines.add((leg['line'], leg['type']))
                        pruned_any = True
                        break
                        
                    leg['wait_time'] = best_wait
                    leg['live_eta'] = best_eta
                    leg['time'] += best_wait
                    cumulative_time += leg['time']
                    
        if pruned_any:
            continue
                
        best_overall_route = {"legs": legs, "route_ids": list(route_ids_used)}
        break
        
    if not best_overall_route:
        return {"success": True, "routes": []}
        
    clean_legs = []
    for leg in best_overall_route['legs']:
        if leg['type'] == 'walk' and clean_legs and clean_legs[-1]['type'] == 'walk':
            clean_legs[-1]['time'] += leg['time']
            clean_legs[-1]['dest_stop'] = leg.get('dest_stop', 'destino')
        else:
            if leg['time'] > 0 or leg['type'] != 'walk': 
                clean_legs.append(leg)

    return {"success": True, "routes": [{"legs": clean_legs, "route_ids": best_overall_route['route_ids']}]}

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
