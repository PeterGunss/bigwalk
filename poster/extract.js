// Renders background/illustration images and measures text positions from poster.html for build_pptx.js
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  fs.mkdirSync(__dirname + '/pptx_assets', { recursive: true });
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage({ viewport: { width: 1240, height: 1754 }, deviceScaleFactor: 3 });
  await p.goto('file://' + __dirname + '/poster.html', { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  const geo = await p.evaluate(() => {
    const r = el => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
    const q = s => r(document.querySelector(s));
    const lecs = [...document.querySelectorAll('.lec')].map(l => ({
      medal: r(l.querySelector('.medal')), meta: r(l.querySelector('.meta')), org: r(l.querySelector('.org')),
      title: r(l.querySelector('.ltitle')), sub: r(l.querySelector('.lsub')), speaker: r(l.querySelector('.speaker')), box: r(l)
    }));
    return { eyebrow: q('.eyebrow'), orn: q('.orn'), h1: q('h1'), sub: q('.sub'), date: q('.date'), header: q('header'),
      lecs, info: q('.info'), intro: q('.intro'), invite: q('.invite'),
      rows: [...document.querySelectorAll('dt')].map(d => ({ dt: r(d), dd: r(d.nextElementSibling) })),
      footer: q('footer'), fname: q('footer .name'), flines: [...document.querySelectorAll('footer .line')].map(r) };
  });
  fs.writeFileSync(__dirname + '/pptx_assets/geo.json', JSON.stringify(geo, null, 1));
  // background: paper, header gradient, footer colour only
  await p.addStyleTag({ content: `header>*,header::after,.lectures,.info,footer>*{visibility:hidden!important} header::after{display:none}` });
  await p.screenshot({ path: __dirname + '/pptx_assets/bg.jpg', type: 'jpeg', quality: 88 });
  // transparent illustrations
  await p.addStyleTag({ content: `html,body,.poster{background:transparent!important}.poster::before{display:none}header,footer{background:transparent!important}
    .lectures,.medal,.medal *,header .orn,header .orn *{visibility:visible!important}.lec>div:not(.medal),.lec>div:not(.medal) *{visibility:hidden!important}.lec{border:none!important}` });
  const meds = await p.$$('.medal');
  for (let i = 0; i < meds.length; i++) {
    const bb = await meds[i].boundingBox(); const pad = 20;
    await p.screenshot({ path: `${__dirname}/pptx_assets/medal${i + 1}.png`, omitBackground: true, clip: { x: bb.x - pad, y: bb.y - pad, width: bb.width + 2 * pad, height: bb.height + 2 * pad } });
  }
  const ob = await (await p.$('.orn')).boundingBox();
  await p.screenshot({ path: __dirname + '/pptx_assets/orn.png', omitBackground: true, clip: ob });
  await b.close();
})();
