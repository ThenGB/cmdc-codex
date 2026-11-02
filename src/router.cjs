const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const zlib = require('node:zlib');
const os = require('node:os');
const home = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
const settings = JSON.parse(fs.readFileSync(path.join(__dirname, 'settings.json')));
const read = p => JSON.parse(fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, ''));
const catalogManager = require('./catalog.cjs').createCatalog(__dirname, settings);
function error(res, status, message) { res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify({error:{message,type:'invalid_request_error'}})); }
const server = http.createServer(async (req, res) => {
  const abort = new AbortController();
  res.on('close', () => { if (!res.writableEnded) abort.abort(); });
  try {
    if (req.url === '/health') { res.setHeader('Content-Type','application/json');res.end(JSON.stringify({service:'codex-commandcode-router-v1',catalog:catalogManager.status()})); return; }
    const auth = read(path.join(home, 'auth.json'));
    const tokens = [auth.tokens?.access_token,auth.OPENAI_API_KEY,process.env.OPENAI_API_KEY].filter(Boolean);
    if (!tokens.some(token => req.headers.authorization === 'Bearer ' + token)) return error(res,401,'Codex authentication required');
    if (req.url === '/refresh' && req.method === 'POST') { await catalogManager.refresh(true);res.setHeader('Content-Type','application/json');res.end(JSON.stringify(catalogManager.status()));return; }
    const buffers = []; let size = 0;
    for await (const chunk of req) { size += chunk.length; if (size > 64*1024*1024) return error(res,413,'Request too large'); buffers.push(chunk); }
    let body = Buffer.concat(buffers); let parsed;
    if (body.length && /application\/json/.test(req.headers['content-type'] || '')) {
      const decoders = {zstd:zlib.zstdDecompressSync,gzip:zlib.gunzipSync,br:zlib.brotliDecompressSync,deflate:zlib.inflateSync};
      const encoding = req.headers['content-encoding'];
      if (encoding && !decoders[encoding]) return error(res,415,'Unsupported content encoding');
      const decoded = encoding ? decoders[encoding](body,{maxOutputLength:64*1024*1024}) : body;
      parsed = JSON.parse(decoded.toString('utf8'));
    }
    const external = parsed?.model?.startsWith('commandcode/');
    let url = settings.nativeBase + req.url.replace(/^\/v1/, '');
    const headers = new Headers();
    for (const [k,v] of Object.entries(req.headers)) if (!['host','connection','content-length','transfer-encoding','accept-encoding','upgrade'].includes(k) && v) headers.set(k,v);
    if (external) {
      if (req.url !== '/v1/responses') return error(res,400,'CommandCode supports /responses here; remote compaction is not advertised');
      const model = (await catalogManager.refresh()).models.find(m => m.slug === parsed.model);
      if (!model) return error(res,400,'Unknown CommandCode model');
      const effort = parsed.reasoning?.effort;
      if (effort && !model.supported_reasoning_levels.some(x => x.effort === effort)) return error(res,400,'Unsupported effort for this model: '+effort);
      parsed.model = parsed.model.slice('commandcode/'.length);
      parsed.input = require('./multimodal.cjs').normalizeToolImages(parsed.input);
      body = Buffer.from(JSON.stringify(parsed));
      // Never forward Codex credentials or account headers to CommandCode.
      for (const k of [...headers.keys()]) headers.delete(k);
      headers.set('Content-Type','application/json');
      headers.set('Authorization','Bearer '+require('./catalog.cjs').getKey(settings));
      url = 'https://api.commandcode.ai/provider/v1/responses';
      fs.appendFileSync(path.join(__dirname,'requests.jsonl'),JSON.stringify({at:new Date().toISOString(),model:parsed.model,effort,stream:parsed.stream,promptCacheKeyPresent:!!parsed.prompt_cache_key})+'\n');
    }
    const upstream = await fetch(url,{method:req.method,headers,body:body.length?body:undefined,signal:abort.signal});
    if (req.method==='GET' && /^\/v1\/models(?:\?|$)/.test(req.url) && upstream.ok) {
      const data = await upstream.json();
      if (!Array.isArray(data.models)) throw Error('Native endpoint did not return a Codex catalog');
      // A CommandCode outage must never remove native authenticated models.
      try { data.models.push(...(await catalogManager.refresh()).models); } catch (_) { /* status exposes the refresh failure */ }
      res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));return;
    }
    const responseHeaders = {};
    upstream.headers.forEach((v,k) => { if (!['content-length','content-encoding','transfer-encoding','connection'].includes(k)) responseHeaders[k]=v; });
    res.writeHead(upstream.status,responseHeaders);
    if (upstream.body) await pipeline(Readable.fromWeb(upstream.body),res); else res.end();
  } catch (e) { if (!res.headersSent) error(res,502,e.message); else res.destroy(); }
});
// HTTP rejection lets Codex use its supported SSE fallback without dropping auth.
server.on('upgrade', (req,socket) => {
  // Desktop can cancel prewarm while the fallback reply is being written.
  socket.on('error', () => socket.destroy());
  socket.end('HTTP/1.1 426 Upgrade Required\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
});
server.listen(settings.port,'127.0.0.1');
