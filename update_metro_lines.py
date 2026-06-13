import json
import asyncio
import aiohttp
import re
import sqlite3

async def fetch_lines(session, stop):
    url = 'https://www.metrovalencia.es/wp-admin/admin-ajax.php'
    data = f"action=formularios_ajax&data=action%3Dinfo-estacion%26id%3D{stop['id']}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded'}
    
    try:
        async with session.post(url, data=data, headers=headers, timeout=10) as r:
            res = await r.json()
            html = res.get('html', '')
            
            # The station details usually have a div with lines right after the title
            # Let's extract all class="linea linea-X" within the header section
            header_html = html.split('<div class="item--proximos">')[0]
            lines = set(re.findall(r'linea-(\d+)', header_html))
            
            return stop['id'], [f'L{line}' for line in sorted(lines)]
    except Exception as e:
        print(f"Error for {stop['id']}: {e}")
        return stop['id'], []

async def main():
    print("Loading metro_stations.json...")
    with open('metro_stations.json', 'r', encoding='utf-8') as f:
        stations = json.load(f)
        
    print(f"Fetching lines for {len(stations)} stations...")
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_lines(session, stop) for stop in stations]
        results = await asyncio.gather(*tasks)
        
    line_map = dict(results)
    
    # Update JSON
    for stop in stations:
        lines = line_map.get(stop['id'], [])
        if not lines:
            lines = ["Metro"] # Fallback
        stop['lines'] = lines
        
    with open('metro_stations.json', 'w', encoding='utf-8') as f:
        json.dump(stations, f, indent=2, ensure_ascii=False)
        
    print("Updated metro_stations.json. Now updating SQLite...")
    
    # Update SQLite
    conn = sqlite3.connect('stops.db')
    c = conn.cursor()
    count = 0
    for stop in stations:
        lines_json = json.dumps(stop['lines'])
        c.execute('UPDATE stops SET lines = ? WHERE id = ?', (lines_json, f"metro-{stop['id']}"))
        count += c.rowcount
    
    conn.commit()
    conn.close()
    print(f"Updated {count} rows in SQLite.")

if __name__ == '__main__':
    asyncio.run(main())
