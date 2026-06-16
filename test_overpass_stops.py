import urllib.request
import urllib.parse
import json

query = """
[out:json][timeout:25];
area["name"="València"]->.searchArea;
(
  relation["network"="MetroBus"](area.searchArea);
  relation["network"="Metrobus"](area.searchArea);
  relation["network"="Metrobús"](area.searchArea);
);
node(r);
out tags;
"""
url = 'https://lz4.overpass-api.de/api/interpreter'
data = urllib.parse.urlencode({'data': query}).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'User-Agent': 'ViaVLC-App/1.0'})
try:
    res = urllib.request.urlopen(req).read().decode('utf-8')
    data = json.loads(res)
    nodes = [e for e in data.get("elements", []) if e["type"] == "node"]
    print("Found nodes:", len(nodes))
    count = 0
    for n in nodes:
        tags = n.get("tags", {})
        if "name" in tags:
            print(f"ID: {n['id']}, Name: {tags.get('name')}, Public Transport: {tags.get('public_transport')}")
            count += 1
            if count > 5:
                break
except Exception as e:
    print(e)
