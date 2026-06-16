import urllib.request
import re

url = "http://metrobus.softoursistemas.com/main-LMRR7OFH.js"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    js_content = urllib.request.urlopen(req).read().decode('utf-8')
    endpoints = re.findall(r'/[a-zA-Z0-9_\-/]+\?[^\s"\']+', js_content)
    for e in set(endpoints):
        print(e)
    # Also look for anything containing 'stop' or 'panel' or 'get'
    for match in re.findall(r'["\'](/[^"\']+)["\']', js_content):
        if 'api' in match or 'stop' in match or 'panel' in match:
            print("Path:", match)
except Exception as e:
    print(e)
