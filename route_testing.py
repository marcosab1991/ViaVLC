import urllib.request
import urllib.parse
import json
import csv

# ==========================================
# ABORDAGEM 1: Overpass API (OpenStreetMap)
# ==========================================
def get_route_overpass(line_ref, network="EMT València"):
    """
    Busca a geometria exata da rota no OpenStreetMap.
    """
    query = f"""
    [out:json][timeout:25];
    relation["network"="{network}"]["ref"="{line_ref}"];
    out geom;
    """
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({'data': query}).encode('utf-8')
    
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            
        features = []
        if 'elements' in result:
            for element in result['elements']:
                if 'members' in element:
                    for member in element['members']:
                        if member['type'] == 'way' and 'geometry' in member:
                            coords = [[pt['lon'], pt['lat']] for pt in member['geometry']]
                            features.append({
                                "type": "Feature",
                                "geometry": {
                                    "type": "LineString",
                                    "coordinates": coords
                                }
                            })
        
        return {
            "type": "FeatureCollection",
            "features": features
        }
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# ABORDAGEM 2: Snap to Road (OSRM)
# ==========================================
def get_route_osrm(stops_coords):
    """
    Faz 'snap' das paragens às estradas usando OSRM.
    stops_coords: list of [lat, lng]
    """
    if len(stops_coords) < 2:
        return {"error": "Need at least 2 stops"}
        
    # OSRM expects: lng,lat;lng,lat
    coords_str = ";".join([f"{lng},{lat}" for lat, lng in stops_coords])
    
    # We can use the public demo server for testing, but it's limited
    url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson&continue_straight=true"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            
        if result.get("code") == "Ok" and result.get("routes"):
            return {
                "type": "Feature",
                "geometry": result["routes"][0]["geometry"]
            }
        else:
            return {"error": result.get("code", "Unknown error")}
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# ABORDAGEM 3: Parse Manual de GTFS (shapes.txt)
# ==========================================
def get_route_gtfs(shapes_txt_path, shape_id):
    """
    Lê o shapes.txt e devolve a geometria da rota (LineString) num GeoJSON limpo.
    """
    try:
        coords = []
        with open(shapes_txt_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("shape_id") == shape_id:
                    coords.append((
                        float(row["shape_pt_lat"]),
                        float(row["shape_pt_lon"]),
                        int(row["shape_pt_sequence"])
                    ))
        
        if not coords:
            return {"error": f"Shape ID '{shape_id}' not found"}
            
        # Ordenar os pontos pela sequência garantindo que a linha é contínua
        coords.sort(key=lambda x: x[2])
        
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat] for lat, lon, _ in coords]
            }
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print("Módulo de Teste de Rotas carregado.")
    # Exemplo de teste simples se tivermos coordenadas de paragens (lat, lng):
    # test_stops = [[39.4699, -0.3763], [39.4720, -0.3780], [39.4750, -0.3800]]
    # res = get_route_osrm(test_stops)
    # print(res)
