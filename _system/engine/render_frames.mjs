// indigo11-video . render_frames.mjs: turn an animated HTML page into a real MOVING video (frame by frame, with Chrome + ffmpeg).
//
//   node _system/engine/render_frames.mjs --page=anim.html --dur=56 --out=silent.mp4 [--fps=30] [--size=1080x1920]
//        [--audio=mix.m4a] [--from=0 --to=56] [--frames-dir=frames] [--quality=90] [--times=1,5.5,12]
//
// THE PAGE CONTRACT (this is what makes the motion deterministic):
//   window.render(t)  draws the WHOLE picture for time t (seconds). It must be a pure function of t:
//                     no setTimeout / requestAnimationFrame / Date / Math.random / CSS transitions or animations.
//   window.ready      set to true once fonts and images have loaded (the renderer waits for it).
//   See _system/engine/animation-starter.html for a working page with pop-ins, constant motion and drawn-on effects.
//
// Needs: Node 22+ (built-in WebSocket and fetch, no npm packages), ffmpeg on PATH, Google Chrome or Edge
// (CHROME_PATH in .env or the environment overrides the search). Frames are JPEG and are piped straight into ffmpeg.
// --times=a,b,c   only screenshots those seconds into <frames-dir> (to look at stills first, no video).
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { join, resolve, dirname, delimiter } from 'node:path';
import { homedir, tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

const A = Object.fromEntries(process.argv.slice(2).filter(a => a.startsWith('--')).map(a => { const i = a.indexOf('='); return i < 0 ? [a.slice(2), true] : [a.slice(2, i), a.slice(i + 1)]; }));
const die = m => { console.error('render_frames: ' + m); process.exit(1); };
if (typeof WebSocket === 'undefined') die('needs Node 22 or newer (found ' + process.version + ').');
if (!A.page) die('missing --page=<file.html>');
if (!A.times && (!A.out || !A.dur)) die('missing --out=<file.mp4> and --dur=<seconds> (or use --times=1,5,9 for stills only)');

const HERE = dirname(fileURLToPath(import.meta.url));
let ROOT = HERE; while (!existsSync(join(ROOT, '_system', 'engine', '_env.py')) && dirname(ROOT) !== ROOT) ROOT = dirname(ROOT);
const WIN = process.platform === 'win32';
const RT = WIN ? join(process.env.LOCALAPPDATA || homedir(), 'indigo11', 'runtime') : join(homedir(), '.indigo11', 'runtime');
process.env.PATH = [join(RT, 'bin'), join(RT, 'node'), process.env.PATH].join(delimiter);

const dotenv = k => { try { const m = readFileSync(join(ROOT, '.env'), 'utf8').match(new RegExp('^\\s*' + k + '\\s*=\\s*(.*)$', 'm')); return m ? m[1].trim().replace(/^["']|["']$/g, '') : ''; } catch { return ''; } };
const localChrome = () => { try { return JSON.parse(readFileSync(join(ROOT, '_system', 'setup', 'local.json'), 'utf8')).chrome || ''; } catch { return ''; } };
const PF = process.env.PROGRAMFILES || 'C:\\Program Files', PF86 = process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)', LOCAL = process.env.LOCALAPPDATA || '';
const SYSTEM = WIN ? [join(PF, 'Google/Chrome/Application/chrome.exe'), join(PF86, 'Google/Chrome/Application/chrome.exe'), join(LOCAL, 'Google/Chrome/Application/chrome.exe'), join(PF86, 'Microsoft/Edge/Application/msedge.exe'), join(PF, 'Microsoft/Edge/Application/msedge.exe')]
  : ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', join(homedir(), 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/microsoft-edge'];
const CHROME = [A.chrome, process.env.CHROME_PATH, dotenv('CHROME_PATH'), localChrome(), ...SYSTEM].find(p => p && existsSync(p));
if (!CHROME) die('Chrome or Edge not found. Install Google Chrome, or put CHROME_PATH=... in the .env file.');

const [W, H] = String(A.size || '1080x1920').split('x').map(Number);
const FPS = +(A.fps || 30), DUR = +(A.dur || 0), Q = +(A.quality || 90);
const T0 = +(A.from || 0), T1 = +(A.to || DUR);
const pageUrl = pathToFileURL(resolve(A.page)).href;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), 'ii-frames-'));   // a FRESH profile each run (a shared one sometimes hangs)

const chrome = spawn(CHROME, ['--headless=new', '--hide-scrollbars', '--mute-audio', '--allow-file-access-from-files', '--disable-gpu-vsync', '--force-color-profile=srgb',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
let ff = null;
const cleanup = () => { try { chrome.kill(); } catch {} try { ff && ff.kill(); } catch {} };
process.on('exit', cleanup);

let tabs;
for (let i = 0; i < 60; i++) { try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (tabs.find(t => t.type === 'page')) break; } catch {} await sleep(250); }
if (!tabs) die('Chrome did not start.');
const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
let id = 0; const pend = {};
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result?.exceptionDetails) die('page error: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 500)); return r.result?.result?.value; };

await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: pageUrl });
let ok = false;
for (let i = 0; i < 150 && !ok; i++) { await sleep(200); try { ok = await evalJs('window.ready === true && document.fonts.status === "loaded"'); } catch {} }
if (!ok) die('the page never set window.ready = true (or fonts did not load).');
if (await evalJs('typeof window.render') !== 'function') die('the page has no window.render(t) function.');
await sleep(300);

const shot = async t => { await evalJs(`window.render(${t})`); const s = await send('Page.captureScreenshot', { format: 'jpeg', quality: Q }); return Buffer.from(s.result.data, 'base64'); };

if (A.times) {   // stills only
  const dir = resolve(A['frames-dir'] || 'stills'); mkdirSync(dir, { recursive: true });
  for (const t of String(A.times).split(',').map(Number)) { writeFileSync(join(dir, `t${t.toFixed(2).padStart(6, '0')}.jpg`), await shot(t)); console.log('still', t); }
  cleanup(); process.exit(0);
}

const out = resolve(A.out); mkdirSync(dirname(out), { recursive: true });
const args = ['-v', 'error', '-y', '-framerate', String(FPS), '-f', 'image2pipe', '-c:v', 'mjpeg', '-i', 'pipe:0'];
if (A.audio) args.push('-i', resolve(A.audio));
args.push('-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-r', String(FPS));
if (A.audio) args.push('-c:a', 'aac', '-b:a', '192k', '-shortest');
args.push('-movflags', '+faststart', out);
ff = spawn('ffmpeg', args, { stdio: ['pipe', 'inherit', 'inherit'] });
const ffDone = new Promise(r => ff.on('close', r));
ff.stdin.on('error', () => {});

const n0 = Math.round(T0 * FPS), n1 = Math.round(T1 * FPS);
const keep = A['frames-dir'] ? (mkdirSync(resolve(A['frames-dir']), { recursive: true }), resolve(A['frames-dir'])) : null;
const started = Date.now();
for (let f = n0; f < n1; f++) {
  const buf = await shot(f / FPS);
  if (keep) writeFileSync(join(keep, String(f).padStart(5, '0') + '.jpg'), buf);
  if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
  if ((f - n0) % 150 === 0) console.log(`frame ${f - n0}/${n1 - n0}  (${Math.round((Date.now() - started) / 1000)} s)`);
}
ff.stdin.end();
const code = await ffDone;
cleanup();
if (code !== 0) die('ffmpeg failed (code ' + code + ').');
console.log(`DONE ${out}  ${n1 - n0} frames, ${((n1 - n0) / FPS).toFixed(1)} s at ${FPS} fps`);
process.exit(0);
