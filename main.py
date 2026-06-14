import asyncio
import json
import re
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
                    'SELECT id, type, name, lat, lng, lines FROM stops WHERE lat >= ? AND lat <= ? AND lng >= ? AND lng <= ?',
                    (min_lat, max_lat, min_lng, max_lng)
                )
            else:
                cursor = await db.execute('SELECT id, type, name, lat, lng, lines FROM stops')
                
            rows = await cursor.fetchall()
            
            stops = []
            for row in rows:
                stops.append({
                    "id": str(row[0]).replace("metro-", ""),  # Strip our internal prefix for the client
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
                    "id": str(row[0]).replace("metro-", ""),
                    "type": row[1],
                    "name": row[2],
                    "location": {"lat": row[3], "lng": row[4]},
                    "lines": json.loads(row[5])
                })
            return {"success": True, "data": stops}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/line_geometry")
async def get_line_geometry(line: str):
    """
    Get OSRM TSP street route and ordered stops for a specific bus line.
    """
    try:
        async with aiosqlite.connect('stops.db') as db:
            cursor = await db.execute(
                'SELECT id, name, lat, lng FROM stops WHERE lines LIKE ? LIMIT 150',
                (f'%"{line}"%',)
            )
            rows = await cursor.fetchall()
            
            if len(rows) == 0:
                return {"success": False, "error": "Not enough stops found for line."}
                
            # Bypass OSRM entirely since the frontend no longer draws the street geometry
            # This prevents OSRM from dropping off-road Metro stations
            ordered_stops = []
            for row in rows:
                ordered_stops.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "lat": row[2],
                    "lng": row[3]
                })
                
            return {
                "success": True, 
                "geometry": None,
                "ordered_stops": ordered_stops
            }
            
    except Exception as e:
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
            
        # Update cache
        eta_cache.set(cache_key, arrivals)
        return {"success": True, "data": arrivals, "cached": False}
        
    except Exception as e:
        print(f"Error fetching ETA for {type} {id}: {e}")
        # Fallback empty list so frontend shows "No data"
        return {"success": True, "data": []}

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
