import urllib.request
import re

url = "http://metrobus.softoursistemas.com/main-LMRR7OFH.js"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    js_content = urllib.request.urlopen(req).read().decode('utf-8')
    for match in re.findall(r'[\'\"`](/[a-zA-Z0-9/\-_]+)[\'\"`]', js_content):
        if 'api' in match.lower() or 'panel' in match.lower() or 'eta' in match.lower() or 'estim' in match.lower():
            print(match)
except Exception as e:
    print(e)
