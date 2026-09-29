// Builds poster.pptx (A4 portrait, editable text) from poster.html measurements. Run extract.js first.
const pptxgen = require('pptxgenjs');
const G = require('./pptx_assets/geo.json');
const A = __dirname + '/pptx_assets/';

const W = 8.27, H = 11.69, S = W / 1240;
const u = v => v * S;              // px -> inch
const f = v => +(v * S * 72).toFixed(1); // px -> pt
const SERIF = 'Noto Serif KR', SANS = 'Noto Sans KR', LATIN = 'Cormorant Garamond';
const C = { ink: '2B201A', ink2: '5A4A3E', ox: '4A1A17', gold: 'B08D57', gold2: 'D6BB85', title: 'F6ECD9', rule: 'B9A27C', foot: 'F3E7CF' };

const pres = new pptxgen();
pres.defineLayout({ name: 'A4P', width: W, height: H });
pres.layout = 'A4P';
pres.title = '2026 임상교육 – 현대 사회 주체의 증상들';
const s = pres.addSlide();
s.background = { path: A + 'bg.jpg' };

const box = (r, pad = 0) => ({ x: u(r.x - pad), y: u(r.y - pad), w: u(r.w + 2 * pad), h: u(r.h + 2 * pad) });
const text = (runs, r, o = {}) => s.addText(runs, { ...box(r, o.pad || 0), margin: 0, isTextBox: true, fontFace: SERIF, valign: 'middle', lang: 'ko-KR', ...o, pad: undefined });
const run = (t, o = {}) => ({ text: t, options: o });

// header frames
s.addShape(pres.shapes.RECTANGLE, { x: u(22), y: u(22), w: u(1196), h: u(G.header.h - 44), fill: { type: 'none' }, line: { color: C.gold2, width: 0.75, transparency: 45 } });
s.addShape(pres.shapes.RECTANGLE, { x: u(30), y: u(30), w: u(1180), h: u(G.header.h - 60), fill: { type: 'none' }, line: { color: C.gold2, width: 0.75, transparency: 75 } });

text([run('2026  ', { fontSize: f(28), bold: true, charSpacing: 2 }),
      run('한국정신분석심리상담협회  임상교육', { fontSize: f(24), charSpacing: 3.7 })],
     G.eyebrow, { align: 'center', color: C.gold2 });
s.addImage({ path: A + 'orn.png', ...box(G.orn) });
text('현대 사회\n주체의 증상들', G.h1, { align: 'center', color: C.title, bold: true, fontSize: f(88), lineSpacingMultiple: 1.0 });
text([run('— 정신분석적 접근 ', { fontSize: f(38) }), run('II', { fontFace: LATIN, fontSize: f(44), bold: true }), run(' —', { fontSize: f(38) })],
     G.sub, { align: 'center', color: C.gold2, charSpacing: 1.5 });
text([run('2026년 11월 7일 토요일'), run('    ◆    ', { color: C.gold, fontSize: f(12) }), run('온라인 '), run('Zoom', { fontFace: LATIN, fontSize: f(32), bold: true }), run(' 강의')],
     G.date, { pad: 30, align: 'center', color: C.title, fontSize: f(27), bold: true });

// lectures
const L = [
  { n: 'I', k: '강의 1', time: '오전 10시 ~ 12시', org: '한국정신분석꿈해석연구회',
    title: [run('대행하는 정신')], sub: [run('AI 시대 욕망, 꿈, 생각, 관계 능력의 위기')],
    who: '이창재', role: '한국정신분석꿈해석연구회 대표 · 프로이트정신분석교육원 원장' },
  { n: 'II', k: '강의 2', time: '오후 1시 30분 ~ 3시 30분', org: '(사)현대정신분석학회',
    title: [run('신경정신분석 '), run('(Neuropsychoanalysis)', { fontFace: LATIN, italic: true, bold: true, fontSize: f(30), color: C.ink2 }), run('의 새로운 길')],
    sub: [run('충동, 예측부호화, 정신증을 중심으로')],
    who: '정경훈', role: '정신분석전문가 · 한국정신분석심리상담협회 이사장' },
  { n: 'III', k: '강의 3', time: '오후 4시 ~ 6시', org: '(사)한국가족복지연구소',
    title: [run('파괴된 내부 대상과 조적 방어')],
    sub: [run('Internal Ruined Object and Manic Defences', { fontFace: LATIN, italic: true, bold: true, fontSize: f(27) })],
    who: '신정인', role: '대상관계 심리상담가 · 수련감독자' },
];
L.forEach((l, i) => {
  const g = G.lecs[i];
  s.addImage({ path: `${A}medal${i + 1}.png`, ...box(g.medal, 20) });
  const metaR = { x: g.meta.x, y: g.meta.y, w: g.org.x - g.meta.x - 10, h: g.meta.h };
  text([run(`Lecture ${l.n}`, { fontFace: LATIN, italic: true, bold: true, fontSize: f(34), color: C.ox }),
        run(`  ${l.k}`, { fontSize: f(19), bold: true, color: C.ox, charSpacing: 1 }),
        run('     |     ', { fontSize: f(16), color: C.rule }),
        run(l.time, { fontSize: f(20), color: C.ink2 })], metaR, { valign: 'bottom' });
  text(l.org, { ...g.org, x: g.org.x - 60, w: g.org.w + 60 }, { align: 'right', valign: 'bottom', fontSize: f(19), color: C.ink2 });
  text(l.title, { ...g.title, w: 870 }, { fontSize: f(35), bold: true, color: C.ink });
  text(l.sub, { ...g.sub, w: 870 }, { fontSize: f(23), color: C.ox });
  text([run(l.who + '  ', { fontFace: SERIF, bold: true, fontSize: f(22), color: C.ink }), run(l.role, { fontFace: SANS, fontSize: f(19), color: C.ink2 })],
       { ...g.speaker, w: 870 });
  if (i < 2) s.addShape(pres.shapes.LINE, { x: u(70), y: u(g.box.y + g.box.h), w: u(1100), h: 0, line: { color: C.rule, width: 0.75 } });
});

// info panel
const I = G.info;
s.addShape(pres.shapes.RECTANGLE, { ...box(I), fill: { color: 'FFFBF2', transparency: 55 }, line: { color: C.rule, width: 0.75 } });
s.addShape(pres.shapes.RECTANGLE, { x: u(I.x + 6), y: u(I.y + 6), w: u(I.w - 12), h: u(I.h - 12), fill: { type: 'none' }, line: { color: C.rule, width: 0.75, transparency: 50 } });
text('한국정신분석심리상담협회 두 번째 임상교육은 “현대 사회 주체의 증상들(정신분석적 접근 II)”이라는 주제를 가지고\n세 곳 단체의 대표 강연자들께서 보석 같은 이론과 깊이 있는 해석을 들려 드릴 예정입니다.',
     G.intro, { align: 'center', fontSize: f(19.5), color: C.ink2, lineSpacingMultiple: 1.25 });
text('한정심 임상교육에 초대합니다', G.invite, { align: 'center', fontSize: f(30), bold: true, color: C.ox, charSpacing: 1.5 });
const b = t => run(t, { bold: true }), m = t => run(t, { color: C.ink2, fontSize: f(17.5) });
const rows = [
  ['일  시', [b('2026년 11월 7일 토요일'), run(' · 온라인 Zoom 강의 '), m('(11/14 ~ 11/15 동영상 재시청)')]],
  ['수강료', [run('강의당 회원 '), b('1만원'), run(' / 비회원 '), b('2만원'), run(' '), m('(강의 선택하여 신청)')]],
  ['신청방법', [run('한정심 홈페이지('), b('kappp.co.kr'), run(') – 특강 연수 신청 페이지 '), m('(신청기간 : 10월 10일 ~ 11월 5일)')]],
  ['비회원', [run('이름 / 소속 / 연락처 / 신청강의 1, 2, 3 선택을 작성하여 이메일('), b('admin@kappp.co.kr'), run(')로 신청', { breakLine: true }),
             m('※ 정/준회원의 경우에도 홈페이지를 통하지 않고 이메일로 신청 시 비회원 교육비 적용')]],
];
rows.forEach(([dt, dd], i) => {
  const g = G.rows[i];
  text(dt, { ...g.dt, h: 30 }, { valign: 'top', fontSize: f(19.5), bold: true, color: C.ox, charSpacing: 2 });
  text(dd, { ...g.dd, w: g.dd.w + 20 }, { valign: 'top', fontFace: SANS, fontSize: f(19.5), color: C.ink, lineSpacingMultiple: 1.2 });
});

// footer
text('한국정신분석심리상담협회 교육위원회', { ...G.fname, x: G.fname.x - 40, w: G.fname.w + 80 }, { align: 'center', fontSize: f(30), bold: true, color: C.foot, charSpacing: 1.5 });
G.flines.forEach(r => s.addShape(pres.shapes.LINE, { x: u(r.x), y: u(r.y), w: u(r.w), h: 0, line: { color: C.gold, width: 0.75 } }));

pres.writeFile({ fileName: __dirname + '/poster.pptx' }).then(() => console.log('written'));
