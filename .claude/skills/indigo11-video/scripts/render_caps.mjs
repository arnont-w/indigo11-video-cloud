// indigo11-video . render_caps.mjs: burn the THAI captions onto the B-roll + mux the audio, with Chrome (no HyperFrames).
// English captions do not use this file: make_short.py burns them with ffmpeg + libass. This one needs node + puppeteer-core + Chrome.
//   node render_caps.mjs --cues=cues.json --broll=broll.mp4 --audio=final_audio.m4a --out=final.mp4 --dur=27.4 [--font=NotoSansThai-VF.ttf] [--fps=30]
// Why Chrome: ffmpeg builds often lack drawtext/subtitles (libass), and Chrome shapes Thai correctly (vowels/tone marks).
// The caption band (y 1090-1570: soft radial shade + 82 px Noto Sans Thai 800, black stroke, pop-in) is screenshotted as a
// transparent PNG per frame; identical frames are reused from a cache, so only the pop-in / fade frames cost a screenshot.
// ffmpeg overlays the band on the 1080x1920 B-roll and muxes the audio in one pass.
import puppeteer from 'puppeteer-core';
import { spawn } from 'node:child_process';
import { readFileSync, existsSync, readdirSync, renameSync, rmSync } from 'node:fs';
import { resolve, dirname, join, delimiter } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
let ROOT = HERE; while (!existsSync(join(ROOT, '_system', 'engine', '_env.py')) && dirname(ROOT) !== ROOT) ROOT = dirname(ROOT);
const WIN = process.platform === 'win32';
const RT = WIN ? join(process.env.LOCALAPPDATA || homedir(), 'indigo11', 'runtime') : join(homedir(), '.indigo11', 'runtime');
process.env.PATH = [join(RT, 'bin'), join(RT, 'node'), process.env.PATH].join(delimiter);
const A = Object.fromEntries(process.argv.slice(2).filter(a => a.startsWith('--')).map(a => { const i = a.indexOf('='); return i < 0 ? [a.slice(2), true] : [a.slice(2, i), a.slice(i + 1)]; }));
for (const k of ['cues', 'broll', 'audio', 'out', 'dur']) if (!A[k]) { console.error(`missing --${k}`); process.exit(1); }
const FPS = +(A.fps || 30), DUR = +A.dur, NF = Math.round(DUR * FPS);
const BAND_Y = 1090, BAND_H = 480;
const cues = JSON.parse(readFileSync(resolve(A.cues), 'utf8'));
const font = readFileSync(resolve(A.font || join(ROOT, 'assets', 'fonts', 'NotoSansThai', 'NotoSansThai-VF.ttf'))).toString('base64');

const dotenv = k => { try { const m = readFileSync(join(ROOT, '.env'), 'utf8').match(new RegExp('^\\s*' + k + '\\s*=\\s*(.*)$', 'm')); return m ? m[1].trim().replace(/^["']|["']$/g, '') : ''; } catch { return ''; } };
const localChrome = () => { try { return JSON.parse(readFileSync(join(ROOT, '_system', 'setup', 'local.json'), 'utf8')).chrome || ''; } catch { return ''; } };
const findShell = () => { const base = join(RT, 'chrome', 'chrome-headless-shell'); try { for (const v of readdirSync(base).sort().reverse()) for (const sub of readdirSync(join(base, v))) {
  const exe = join(base, v, sub, WIN ? 'chrome-headless-shell.exe' : 'chrome-headless-shell'); if (existsSync(exe)) return exe; } } catch {} return null; };
const PF = process.env.PROGRAMFILES || 'C:\\Program Files', PF86 = process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)', LOCAL = process.env.LOCALAPPDATA || '';
const SYSTEM = WIN ? [join(PF, 'Google/Chrome/Application/chrome.exe'), join(PF86, 'Google/Chrome/Application/chrome.exe'), join(LOCAL, 'Google/Chrome/Application/chrome.exe'), join(PF86, 'Microsoft/Edge/Application/msedge.exe'), join(PF, 'Microsoft/Edge/Application/msedge.exe')]
  : ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', join(homedir(), 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), '/usr/bin/google-chrome', '/usr/bin/chromium'];
const CHROME = [A.chrome, process.env.CHROME_PATH, dotenv('CHROME_PATH'), localChrome(), ...SYSTEM, findShell()].find(p => p && existsSync(p));
if (!CHROME) { console.error('Chrome not found for rendering. Install Google Chrome, or put CHROME_PATH=... in the .env file.'); process.exit(1); }

const html = `<!doctype html><html lang="th"><head><meta charset="utf-8"><style>
@font-face { font-family:'Noto Sans Thai'; src:url(data:font/ttf;base64,${font}) format('truetype'); font-weight:100 900; font-display:block; }
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:1080px; height:${BAND_H}px; overflow:hidden; background:transparent; }
#capbg { position:absolute; left:0; top:0; width:1080px; height:${BAND_H}px; pointer-events:none;
  background:radial-gradient(ellipse 70% 45% at 50% 50%, rgba(0,0,0,.62) 0%, rgba(0,0,0,.45) 45%, rgba(0,0,0,.18) 72%, rgba(0,0,0,0) 100%); }
#caps { position:absolute; left:0; top:${1180 - BAND_Y}px; width:1080px; height:300px; display:flex; align-items:center; justify-content:center; }
.cue { position:absolute; opacity:0; width:940px; text-align:center; font-family:'Noto Sans Thai',sans-serif; font-weight:800; font-size:82px;
  line-height:1.25; color:#fff; -webkit-text-stroke:10px #000; paint-order:stroke fill; text-shadow:0 6px 20px rgba(0,0,0,.6); }
</style></head><body><div id="capbg"></div><div id="caps"></div></body></html>`;

// GSAP-equivalent timing of the kit: in 0.17 s back.out(2.8) from opacity 0, scaleX .5, scaleY .7; out 0.07 s power2.in to opacity 0, scale .93
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const backOut = (p, s = 2.8) => { p -= 1; return p * p * ((s + 1) * p + s) + 1; };
const state = t => cues.map((c, i) => {
  if (t < c.t || t > c.e) return null;
  const kin = backOut(clamp((t - c.t) / 0.17, 0, 1)), q = clamp((t - (c.e - 0.07)) / 0.07, 0, 1) ** 2;
  let op = Math.min(1, kin), sx = 0.5 + 0.5 * kin, sy = 0.7 + 0.3 * kin;
  if (q > 0) { op *= 1 - q; sx *= 1 - 0.07 * q; sy *= 1 - 0.07 * q; }
  return op <= 0.001 ? null : [i, +op.toFixed(3), +sx.toFixed(3), +sy.toFixed(3)];
}).filter(Boolean);

const browser = await puppeteer.launch({ executablePath: CHROME, headless: /headless-shell/.test(CHROME) ? 'shell' : true, args: ['--hide-scrollbars', '--mute-audio', '--force-color-profile=srgb'] });
const page = await browser.newPage();
await page.setViewport({ width: 1080, height: BAND_H, deviceScaleFactor: 1 });
await page.setContent(html, { waitUntil: 'load' });
await page.evaluate(async list => {
  const box = document.getElementById('caps');
  for (const c of list) { const d = document.createElement('div'); d.className = 'cue'; d.textContent = c.txt; box.appendChild(d); }
  await document.fonts.load("800 82px 'Noto Sans Thai'"); await document.fonts.ready;
}, cues);

const cache = new Map();
const shot = async st => {
  const key = JSON.stringify(st);
  if (cache.has(key)) return cache.get(key);
  await page.evaluate(st => {
    const els = document.querySelectorAll('.cue'); els.forEach(e => { e.style.opacity = 0; });
    for (const [i, op, sx, sy] of st) { els[i].style.opacity = op; els[i].style.transform = `scale(${sx}, ${sy})`; }
  }, st);
  const b = Buffer.from(await page.screenshot({ type: 'png', omitBackground: true }));
  cache.set(key, b); return b;
};

const OUT = resolve(A.out), PART = OUT.replace(/\.mp4$/i, '') + '.partial.mp4';
rmSync(PART, { force: true });
const ff = spawn('ffmpeg', ['-y', '-v', 'error', '-i', resolve(A.broll), '-i', resolve(A.audio),
  '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
  '-filter_complex', `[0:v]fps=${FPS},setsar=1[b];[b][2:v]overlay=0:${BAND_Y}:format=auto:eof_action=pass,format=yuv420p[v]`,
  '-map', '[v]', '-map', '1:a', '-t', DUR.toFixed(3), '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p',
  '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-f', 'mp4', PART], { stdio: ['pipe', 'inherit', 'inherit'] });
const done = new Promise((ok, bad) => ff.on('close', c => c ? bad(new Error('ffmpeg exited ' + c)) : ok()));
ff.stdin.on('error', () => {});
const t0 = Date.now();
for (let f = 0; f < NF; f++) {
  const b = await shot(state(f / FPS));
  if (!ff.stdin.write(b)) await new Promise(r => ff.stdin.once('drain', r));
  if (f % 150 === 0) process.stdout.write(`frame ${f}/${NF}\r`);
}
ff.stdin.end();
await browser.close();
try { await done; } catch (e) { console.error('RENDER FAILED: ' + e.message); process.exit(1); }
renameSync(PART, OUT);
console.log(`${NF} frames (${cache.size} distinct caption frames) in ${((Date.now() - t0) / 1000).toFixed(1)} s -> ${OUT}`);
