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
out tags;
"""
url = 'https://lz4.overpass-api.de/api/interpreter'
data = urllib.parse.urlencode({'data': query}).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'User-Agent': 'ViaVLC-App/1.0'})
try:
    res = urllib.request.urlopen(req).read().decode('utf-8')
    data = json.loads(res)
    print("Found relations:", len(data.get("elements", [])))
    for elem in data.get("elements", [])[:5]:
        tags = elem.get("tags", {})
        print(f"Ref: {tags.get('ref')}, Name: {tags.get('name')}, Network: {tags.get('network')}")
except Exception as e:
    print(e)
