import requests
url = "https://geoportal.valencia.es/server/rest/services/OPENDATA/Trafico/MapServer/226/query?where=id_parada=2289&outFields=*&f=json"
response = requests.get(url, timeout=10)
data = response.json()
print("Features:", data.get("features", []))
