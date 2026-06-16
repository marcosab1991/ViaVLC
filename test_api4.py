import urllib.request
import json

base_url = "http://metrobus.softoursistemas.com"
endpoints = [
    "/api/stop/405",
    "/api/v1/stop/405",
    "/server/stop/405",
    "/api/eta/405"
]

for endpoint in endpoints:
    url = base_url + endpoint
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        res = urllib.request.urlopen(req)
        print("Success:", endpoint, res.getcode())
    except Exception as e:
        print("Failed:", endpoint, e)
