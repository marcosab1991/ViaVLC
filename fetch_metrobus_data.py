import urllib.request
import urllib.parse
import json
import os

def fetch_metrobus_data():
    print("Fetching Metrobus data from OpenStreetMap...")
    query = """
    [out:json][timeout:50];
    area["name"="València"]->.searchArea;
    (
      relation["network"="MetroBus"](area.searchArea);
      relation["network"="Metrobus"](area.searchArea);
      relation["network"="Metrobús"](area.searchArea);
    );
    out geom;
    node(r);
    out;
    """
    
    url = 'https://lz4.overpass-api.de/api/interpreter'
    data = urllib.parse.urlencode({'data': query}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'ViaVLC-App/1.0'})
    
    try:
        res = urllib.request.urlopen(req).read().decode('utf-8')
        osm_data = json.loads(res)
        
        # Save raw data for processing geometries
        with open('metrobus_routes_raw.json', 'w', encoding='utf-8') as f:
            json.dump(osm_data, f, ensure_ascii=False, indent=2)
            
        print(f"Downloaded {len(osm_data.get('elements', []))} elements.")
        
        nodes = {}
        ways = {}
        relations = []
        
        for elem in osm_data.get('elements', []):
            if elem['type'] == 'node':
                nodes[elem['id']] = elem
            elif elem['type'] == 'way':
                ways[elem['id']] = elem
            elif elem['type'] == 'relation':
                relations.append(elem)
                
        stops = {}
        routes = []
        
        for rel in relations:
            tags = rel.get('tags', {})
            ref = tags.get('ref', 'Unknown')
            name = tags.get('name', '')
            
            routes.append(rel)
            
            for member in rel.get('members', []):
                if member['type'] == 'node' and member['role'] in ['stop', 'platform', 'platform_edge', 'stop_entry_only', 'stop_exit_only', '']:
                    node_id = member['ref']
                    if node_id in nodes:
                        node = nodes[node_id]
                        tags = node.get('tags', {})
                        stop_code = tags.get('ref', str(node_id))
                        if stop_code not in stops:
                            stops[stop_code] = {
                                'id': stop_code,
                                'lat': node['lat'],
                                'lon': node['lon'],
                                'name': tags.get('name', f"Parada {stop_code}"),
                                'lines': set()
                            }
                        stops[stop_code]['lines'].add(ref)
        
        formatted_stops = []
        for stop in stops.values():
            stop['lines'] = sorted(list(stop['lines']))
            formatted_stops.append(stop)
            
        with open('metrobus_stations.json', 'w', encoding='utf-8') as f:
            json.dump(formatted_stops, f, ensure_ascii=False, indent=2)
            
        print(f"Extracted {len(formatted_stops)} Metrobus stops and {len(routes)} routes.")
        
    except Exception as e:
        print("Error fetching Metrobus data:", e)

if __name__ == '__main__':
    fetch_metrobus_data()
