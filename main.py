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
            
            # Fallback for Metrobus: strip trailing letter (e.g. 161A -> 161) if route not found
            if not rows and type == "metrobus" and db_line_ref and db_line_ref[-1].isalpha():
                base_ref = "".join([c for c in db_line_ref if c.isdigit()])
                cursor = await db.execute('SELECT destination, geometry_json FROM routes WHERE ref=? AND type=?', (base_ref, type))
                rows = await cursor.fetchall()
                
            if rows:
                if destination:
                    best_geom = None
                    highest_ratio = -1
                    target_dest = remove_accents(destination.lower())
                    
                    aliases = {
                        "est.del nord": "xativa",
                        "estacio del nord": "xativa",
                        "est. del nord": "xativa"
                    }
                    if target_dest in aliases:
                        target_dest = aliases[target_dest]
                    
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
            
            # Fallback for Metrobus stops: strip trailing letter (e.g. 161A -> 161) if no stops found
            if not rows and type == "metrobus" and line and line[-1].isalpha():
                base_line = "".join([c for c in line if c.isdigit()])
                cursor = await db.execute(
                    'SELECT id, name, lat, lng FROM stops WHERE type=? AND lines LIKE ? LIMIT 150',
                    (type, f'%"{base_line}"%')
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

async def fetch_metro_eta(stop_id: str):
    stop_id = stop_id.replace("metro-", "")
    url = 'https://www.metrovalencia.es/wp-admin/admin-ajax.php'
    inner_data = f'action=info-estacion&id={stop_id}'
    data = {'action': 'formularios_ajax', 'data': inner_data}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10) as response:
            text = await response.text()
            try:
                res = json.loads(text)
            except Exception:
                res = {}
            html = res.get('html', '')
            
            arrivals = []
            blocks = html.split('item--proximos')[1:]
            for block in blocks:
                line_match = re.search(r'class=\"linea linea-(\w+)\"', block)
                dest_match = re.search(r'<div class=\"nombre-estacion\">(.*?)</div>', block)
                eta_match = re.search(r'<span class=\"minutos[^\"]*\">(.*?)</span>', block)
                if line_match and dest_match and eta_match:
                    arrivals.append({
                        "line": f"L{line_match.group(1)}",
                        "destination": dest_match.group(1).strip(),
                        "eta": eta_match.group(1).strip()
                    })
            return arrivals

async def fetch_tram_eta(stop_id: str):
    stop_id = stop_id.replace("tram-", "")
    url = 'https://www.tramalacant.es/wp-admin/admin-ajax.php'
    inner_data = f'action=info-estacion&id={stop_id}'
    data = {'action': 'formularios_ajax', 'data': inner_data}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10) as response:
            text = await response.text()
            try:
                res = json.loads(text)
            except Exception:
                res = {}
            html = res.get('html', '')
            
            arrivals = []
            blocks = html.split('item--proximos')[1:]
            for block in blocks:
                line_match = re.search(r'class=\"linea linea-(\w+)\"', block)
                dest_match = re.search(r'<div class=\"nombre-estacion\">(.*?)</div>', block)
                eta_match = re.search(r'<span class=\"minutos[^\"]*\">(.*?)</span>', block)
                if line_match and dest_match and eta_match:
                    arrivals.append({
                        "line": f"L{line_match.group(1)}",
                        "destination": dest_match.group(1).strip(),
                        "eta": eta_match.group(1).strip()
                    })
            return arrivals

def fetch_bus_eta_sync(stop_id: str):
    arrivals = []
    live_data = emtvlcapi.get_bus_times(int(stop_id))
    if live_data:
        for item in live_data:
            arrivals.append({
                "line": str(item.get("linea", "")),
                "eta": f"{item.get('minutos', '?')} min",
                "destination": item.get("destino", "")
            })
    return arrivals

async def fetch_metrobus_eta(stop_id: str):
    """
    Fetch ETA from Metrobus Softour API. If real-time is empty, fallback to schedules!
    """
    try:
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

@app.get("/api/eta")
async def get_eta(id: str, type: str):
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
        # Fallback empty list so frontend shows "No data"
        return {"success": True, "data": []}

def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

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
    MAX_WALK = 600
    WALK_SPEED = 4.0 * 1000 / 60 # 66.66 meters per minute (reduced from 5km/h to 4km/h)
    SPEEDS = {'metro': 25000/60, 'tram': 20000/60, 'bus': 15000/60, 'metrobus': 15000/60} # meters per minute
    
    try:
        async with aiosqlite.connect('stops.db') as db:
            cursor = await db.execute("SELECT id, type, name, lat, lng FROM stops")
            all_stops = await cursor.fetchall()
            
        orig_stops = {}
        dest_stops = {}
        for row in all_stops:
            sid, stype, sname, slat, slng = row
            # Calculate physical straight-line distance, but apply 1.4 detour factor to simulate street grid
            d_orig = calculate_haversine(orig_lat, orig_lng, slat, slng) * 1.4
            d_dest = calculate_haversine(dest_lat, dest_lng, slat, slng) * 1.4
            if d_orig <= MAX_WALK * 1.4: # Allow physical 600m radius even with detour factor
                orig_stops[sid] = {'dist': d_orig, 'name': sname, 'type': stype, 'lat': slat, 'lng': slng}
            if d_dest <= MAX_WALK * 1.4:
                dest_stops[sid] = {'dist': d_dest, 'name': sname, 'type': stype, 'lat': slat, 'lng': slng}
                
        if not orig_stops or not dest_stops:
            return {"success": True, "routes": []}
            
        routes_found = []
        
        async with aiosqlite.connect('lines.db') as db:
            cursor = await db.execute("SELECT route_id, stops_json FROM route_stops")
            for rid, stops_json in await cursor.fetchall():
                seq = json.loads(stops_json)
                
                # Find all orig and dest stops in this sequence
                seq_origs = [s for s in seq if s['id'] in orig_stops]
                seq_dests = [s for s in seq if s['id'] in dest_stops]
                
                if not seq_origs or not seq_dests:
                    continue
                    
                # Find best pair (origin MUST be before destination)
                best_pair = None
                best_total_dist = float('inf')
                
                for so in seq_origs:
                    for sd in seq_dests:
                        if so['dist_along'] < sd['dist_along']:
                            # Valid direction!
                            # Distance in transit
                            transit_dist = sd['dist_along'] - so['dist_along']
                            
                            t_type = orig_stops[so['id']]['type']
                            t_baseline = transit_dist / SPEEDS.get(t_type, 15000/60)
                            walk1 = orig_stops[so['id']]['dist'] / WALK_SPEED
                            walk2 = dest_stops[sd['id']]['dist'] / WALK_SPEED
                            
                            # Weight the total theoretical time (not raw meters!) to find the best pair
                            theoretical_time = t_baseline + walk1 + walk2
                            if theoretical_time < best_total_dist:
                                best_total_dist = theoretical_time
                                best_pair = (so, sd, transit_dist, t_baseline, walk1, walk2)
                                
                if best_pair:
                    so, sd, transit_dist, t_baseline, walk1, walk2 = best_pair
                    
                    os_info = orig_stops[so['id']]
                    ds_info = dest_stops[sd['id']]
                    t_type = os_info['type']
                    
                    # Fetch ETAs!
                    # For ETA we need to extract line ref to match
                    cursor_routes = await db.execute("SELECT ref, name, destination FROM routes WHERE id=?", (rid,))
                    route_info = await cursor_routes.fetchone()
                    if not route_info: continue
                    r_ref, r_name, r_dest = route_info
                    
                    routes_found.append({
                        'route_id': rid,
                        'line': r_ref,
                        'name': r_name,
                        'destination': r_dest,
                        'type': t_type,
                        'orig_stop': {'id': so['id'], 'name': os_info['name'], 'walk': walk1, 'lat': os_info['lat'], 'lng': os_info['lng']},
                        'dest_stop': {'id': sd['id'], 'name': ds_info['name'], 'walk': walk2, 'lat': ds_info['lat'], 'lng': ds_info['lng']},
                        'transit_dist': transit_dist,
                        't_baseline': t_baseline
                    })
                    
        # Now process ETAs
        valid_routes = []
        for r in routes_found:
            t_type = r['type']
            l_ref = r['line']
            
            # Use the cached / internal ETA fetcher
            eta_orig_res = await get_eta(r['orig_stop']['id'], t_type)
            eta_dest_res = await get_eta(r['dest_stop']['id'], t_type)
            
            o_etas = eta_orig_res.get('data', []) if eta_orig_res.get('success') else []
            d_etas = eta_dest_res.get('data', []) if eta_dest_res.get('success') else []
            
            # Filter by line
            o_etas = [e for e in o_etas if str(e.get('line')) == str(l_ref)]
            d_etas = [e for e in d_etas if str(e.get('line')) == str(l_ref)]
            
            best_t_wait = None
            best_t_transit = r['t_baseline']
            is_realtime = False
            
            if o_etas:
                # Pick the first vehicle arriving
                t_wait = parse_time_str(o_etas[0].get('eta'))
                is_schedule_data = not o_etas[0].get('realtime', True) # False if it's from schedule fallback
                
                if t_wait != float('inf'):
                    best_t_wait = t_wait
                    
                    # Try to correlate at destination!
                    expected_dest_eta = t_wait + r['t_baseline']
                    best_diff = float('inf')
                    for de in d_etas:
                        dt = parse_time_str(de.get('eta'))
                        if dt != float('inf') and dt >= t_wait:
                            diff = abs(dt - expected_dest_eta)
                            if diff < best_diff and diff < 20: # Must be within 20 mins of baseline
                                best_diff = diff
                                best_t_transit = dt - t_wait
                                is_realtime = not is_schedule_data # Only true if it's actual GPS real-time
                                
            if best_t_wait is None:
                # User requested to ONLY show routes with a real or scheduled ETA at the origin stop!
                continue
                
            r['t_wait'] = best_t_wait
            r['t_transit'] = best_t_transit
            r['t_total'] = r['orig_stop']['walk'] + best_t_wait + best_t_transit + r['dest_stop']['walk']
            r['is_realtime'] = is_realtime
            
            # Penalize routes that couldn't correlate the destination ETA
            r['sort_score'] = r['t_total'] if is_realtime else r['t_total'] + 1000
            valid_routes.append(r)
            
        valid_routes.sort(key=lambda x: x['sort_score'])
        
        # Filter duplicates (same origin and destination stops) to prevent corridor clogging
        unique_routes = []
        seen = set()
        for r in valid_routes:
            k = f"{r['orig_stop']['id']}-{r['dest_stop']['id']}"
            if k not in seen:
                seen.add(k)
                unique_routes.append(r)
                
        # Phase 5: Fetch real OSRM walk times for the top 10 candidate routes
        top_candidates = unique_routes[:10]
        
        async def enrich_route(r):
            so_lat, so_lng = r['orig_stop']['lat'], r['orig_stop']['lng']
            sd_lat, sd_lng = r['dest_stop']['lat'], r['dest_stop']['lng']
            
            w1_time, w1_dist = await fetch_osrm_walk(orig_lat, orig_lng, so_lat, so_lng)
            w2_time, w2_dist = await fetch_osrm_walk(sd_lat, sd_lng, dest_lat, dest_lng)
            
            r['orig_stop']['walk'] = w1_time
            r['dest_stop']['walk'] = w2_time
            
            r['t_total'] = w1_time + r['t_wait'] + r['t_transit'] + w2_time
            r['sort_score'] = r['t_total'] if r['is_realtime'] else r['t_total'] + 1000
            return r
            
        if top_candidates:
            final_routes = await asyncio.gather(*(enrich_route(r) for r in top_candidates))
            final_routes.sort(key=lambda x: x['sort_score'])
        else:
            final_routes = []
                
        return {"success": True, "routes": final_routes} # Top unique routes updated with OSRM
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Journey API Error: {e}")
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
