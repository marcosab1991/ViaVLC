import urllib.request
import re
url = "http://metrobus.softoursistemas.com/main-LMRR7OFH.js"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    js = urllib.request.urlopen(req).read().decode('utf-8')
    # Search for api domains
    print(set(re.findall(r'https?://[a-zA-Z0-9\-\.]+\.com/api/[^\s"\']+', js)))
    print(set(re.findall(r'https?://[a-zA-Z0-9\-\.]+\.[a-z]{2,3}/[^\s"\']+', js)))
except Exception as e:
    print(e)
