import json
import time
import re
import sqlite3
import urllib.request

def main():
    print("Loading metro_stations.json...")
    with open('metro_stations.json', 'r', encoding='utf-8') as f:
        stations = json.load(f)
        
    print(f"Fetching lines for {len(stations)} stations slowly (2 second pause between each)...")
    
    line_map = {}
    
    for idx, stop in enumerate(stations):
        url = 'https://www.metrovalencia.es/wp-admin/admin-ajax.php'
        data = f"action=formularios_ajax&data=action%3Dinfo-estacion%26id%3D{stop['id']}".encode('utf-8')
        headers = {'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded'}
        
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            html = res.get('html', '')
            
            header_html = html.split('<div class="item--proximos">')[0]
            lines = set(re.findall(r'linea-(\d+)', header_html))
            
            parsed_lines = [f'L{line}' for line in sorted(lines)]
            line_map[stop['id']] = parsed_lines
            print(f"[{idx+1}/{len(stations)}] Station {stop['name']}: {parsed_lines}")
        except Exception as e:
            print(f"[{idx+1}/{len(stations)}] Error for {stop['name']} ({stop['id']}): {e}")
            line_map[stop['id']] = []
            
        # Pause to avoid getting blocked by Metrovalencia servers
        time.sleep(2)
    
    # Update JSON
    for stop in stations:
        lines = line_map.get(stop['id'], [])
        if not lines:
            lines = ["Metro"] # Fallback
        stop['lines'] = lines
        
    with open('metro_stations.json', 'w', encoding='utf-8') as f:
        json.dump(stations, f, indent=2, ensure_ascii=False)
        
    print("\nUpdated metro_stations.json. Now updating SQLite...")
    
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
    print("Done! Restart the web server for the changes to take effect.")

if __name__ == '__main__':
    main()
