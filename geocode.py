import urllib.request
import json

url = "https://nominatim.openstreetmap.org/search?q=Carrer+de+Roger+de+Flor,+Valencia&format=json"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ValenciaTransitMap/1.0'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        if data:
            print("Lat:", data[0]['lat'], "Lon:", data[0]['lon'])
        else:
            print("Not found")
except Exception as e:
    print(e)
