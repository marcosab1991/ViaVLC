import urllib.request
import urllib.parse
import json
import bs4
import difflib

def fetch_tram_data():
    print("Scraping IDs from TRAM Alicante...")
    url = 'https://www.tramalacant.es/ca/consulta-estacions/'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req).read().decode('utf-8')
    soup = bs4.BeautifulSoup(html, 'html.parser')
    select = soup.find('select')
    id_map = {}
    for o in select.find_all('option'):
        if o.get('value'):
            id_map[o.text.strip()] = o.get('value')
    print(f"Scraped {len(id_map)} IDs.")

    print("Querying Overpass for TRAM stations and routes...")
    query = """
    [out:json][timeout:60];
    relation["network"="TRAM Metropolità d'Alacant"]["type"="route"];
    out geom;
    node(r);
    out;
    """
    url_overpass = 'https://lz4.overpass-api.de/api/interpreter'
    data = urllib.parse.urlencode({'data': query}).encode('utf-8')
    req = urllib.request.Request(url_overpass, data=data, headers={'User-Agent': 'ViaVLC-App/1.0'})
    res = json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))
    
    routes_json = {"elements": []}
    osm_nodes = []
    
    for elem in res.get('elements', []):
        if elem['type'] == 'relation':
            routes_json["elements"].append(elem)
        elif elem['type'] == 'node':
            osm_nodes.append(elem)
            
    osm_stations = [n for n in osm_nodes if 'name' in n.get('tags', {})]
    print(f"Overpass stations found: {len(osm_stations)}")

    matched_stations = []
    for s in osm_stations:
        name = s.get('tags', {}).get('name', '')
        # Special case handling for TRAM names
        clean_name = name.replace("Estación de ", "").replace("Estació de ", "")
        
        hardcoded_matches = {
            "Olla Altea": "Olla de Altea / l'Olla - Altea",
            "Olla de Altea": "Olla de Altea / l'Olla - Altea",
            "l'Olla": "Olla de Altea / l'Olla - Altea",
            "l'Olla d'Altea": "Olla de Altea / l'Olla - Altea"
        }
        
        if clean_name in hardcoded_matches:
            matches_found = [hardcoded_matches[clean_name]]
        else:
            matches_found = difflib.get_close_matches(clean_name, id_map.keys(), n=1, cutoff=0.5)
        if matches_found:
            matched_id = id_map[matches_found[0]]
            matched_stations.append({
                "id": str(matched_id),
                "type": "tram",
                "name": matches_found[0],
                "location": {
                    "lat": s["lat"],
                    "lng": s["lon"]
                },
                "lines": [] # Lines will be inferred or we can just leave empty as TRAM ETA returns line anyway
            })
    
    print(f"Successfully matched {len(matched_stations)} stations.")
    
    with open('tram_stations.json', 'w') as f:
        json.dump(matched_stations, f, indent=2)
    print("Saved tram_stations.json")
    
    with open('tram_routes.json', 'w') as f:
        json.dump(routes_json, f, indent=2)
    print("Saved tram_routes.json")

if __name__ == "__main__":
    fetch_tram_data()
