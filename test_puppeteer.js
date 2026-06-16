const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  page.on('request', request => {
    const url = request.url();
    if (url.includes('api') || url.includes('json') || url.includes('softour') || url.includes('panel')) {
      console.log('Request:', url);
    }
  });
  await page.goto('http://metrobus.softoursistemas.com/stop/panel?stop=405&stopName=Corts%20Valencianes%20-%20Escola%20Professional%20de%20Sant%20Josep&stopCode=405&dir=0&lon=-0.395985&lat=39.485033', { waitUntil: 'networkidle0' });
  await browser.close();
})();
