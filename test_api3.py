import urllib.request
import re

url = "http://metrobus.softoursistemas.com/main-LMRR7OFH.js"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    js_content = urllib.request.urlopen(req).read().decode('utf-8')
    # Find base url variables or properties
    for match in re.findall(r'([a-zA-Z0-9_]+\.apiUrl\s*=\s*[\'\"`][^\'\"`]+[\'\"`])', js_content):
        print("API URL assignment:", match)
    for match in re.findall(r'(https?://[^\s\'"]*api[^\s\'"]*)', js_content):
        print("API in string:", match)
except Exception as e:
    print(e)
