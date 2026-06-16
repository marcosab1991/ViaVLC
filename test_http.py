import urllib.request
import json
try:
    req = urllib.request.Request('http://127.0.0.1:8000/api/line_geometry?line=4&type=metro')
    res = urllib.request.urlopen(req)
    data = json.loads(res.read().decode('utf-8'))
    print("Success:", data.get("success"))
    print("Has geometry:", "geometry" in data and data["geometry"] is not None)
    if data.get("geometry"):
        print("Geom type:", data["geometry"].get("type"))
        print("Coords len:", len(data["geometry"].get("coordinates", [])))
except Exception as e:
    print("Port 8000 Error:", e)

try:
    req = urllib.request.Request('http://127.0.0.1:5000/api/line_geometry?line=4&type=metro')
    res = urllib.request.urlopen(req)
    data = json.loads(res.read().decode('utf-8'))
    print("Success:", data.get("success"))
    print("Has geometry:", "geometry" in data and data["geometry"] is not None)
    if data.get("geometry"):
        print("Geom type:", data["geometry"].get("type"))
        print("Coords len:", len(data["geometry"].get("coordinates", [])))
except Exception as e:
    print("Port 5000 Error:", e)
