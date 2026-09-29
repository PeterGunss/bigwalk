const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage({ viewport: { width: 1240, height: 1754 }, deviceScaleFactor: 2 });
  await p.goto('file://' + __dirname + '/poster.html', { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  await p.screenshot({ path: __dirname + '/poster.png' });
  await p.pdf({ path: __dirname + '/poster.pdf', width: '1240px', height: '1754px', printBackground: true });
  await b.close();
})();
