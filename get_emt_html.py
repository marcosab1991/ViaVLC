import urllib.request
import urllib.parse

def fetch():
    url = "https://emtvalencia.es/ciudadano/modules/mod_tiempo/saca_tiempos_ajax.php"
    data = urllib.parse.urlencode({'parada': '397', 'idioma': 'es'}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.read().decode('utf-8')[:500])
    except Exception as e:
        print(e)
fetch()
