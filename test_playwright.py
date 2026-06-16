from playwright.sync_api import sync_playwright

def run(playwright):
    browser = playwright.firefox.launch(headless=True)
    page = browser.new_page()
    
    # Intercept network requests
    def handle_request(request):
        if 'api' in request.url or 'eta' in request.url or 'estim' in request.url or 'server' in request.url or request.resource_type in ['fetch', 'xhr']:
            print("Intercepted:", request.url)
            
    page.on("request", handle_request)
    page.goto("http://metrobus.softoursistemas.com/stop/panel?stop=405&stopName=Corts%20Valencianes%20-%20Escola%20Professional%20de%20Sant%20Josep&stopCode=405&dir=0&lon=-0.395985&lat=39.485033", wait_until="networkidle")
    
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
