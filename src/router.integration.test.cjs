const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net'),cp=require('node:child_process'),zlib=require('node:zlib');

async function freePort(){
 return new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
}
async function startRouter(t,mockLines,nativeBase='http://127.0.0.1:9/v1',options={}){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-router-integration-'));
 const port=await freePort(),codexHome=path.join(dir,'codex-home'),commandHome=path.join(dir,'commandcode-home');
 for(const file of ['router.cjs','catalog.cjs','multimodal.cjs'])fs.copyFileSync(path.join(__dirname,file),path.join(dir,file));
 fs.mkdirSync(codexHome);fs.mkdirSync(commandHome,{recursive:true});
 const codexToken='integration-codex-token',commandKey='integration-command-key';
 if(!options.noAuth)fs.writeFileSync(path.join(codexHome,'auth.json'),JSON.stringify({tokens:{access_token:codexToken,account_id:'account-fixture'}}));
 fs.writeFileSync(path.join(commandHome,'credentials.json'),JSON.stringify({apiKey:commandKey}));
 fs.writeFileSync(path.join(dir,'settings.json'),JSON.stringify({port,nativeBase,keyFile:path.join(commandHome,'credentials.json')}));
 fs.writeFileSync(path.join(dir,'mock-fetch.cjs'),mockLines.join(''));
 const child=cp.spawn(process.execPath,[path.join(dir,'router.cjs')],{env:{...process.env,CODEX_HOME:codexHome,USERPROFILE:commandHome,NODE_OPTIONS:'--require '+path.join(dir,'mock-fetch.cjs')},windowsHide:true});
 let stderr='';child.stderr.on('data',chunk=>stderr+=chunk);
 t.after(async()=>{const closed=new Promise(resolve=>child.once('close',resolve));child.kill();await closed;fs.rmSync(dir,{recursive:true,force:true});});
 for(let attempt=0;attempt<60;attempt++){
  try{if((await fetch('http://127.0.0.1:'+port+'/health')).ok)return {child,dir,port,codexToken,commandKey,stderr:()=>stderr};}catch{}
  await new Promise(resolve=>setTimeout(resolve,50));
 }
 throw Error('Router failed to start: '+stderr);
}
function catalogMockLines(captureFile,{inferenceResponse=null,nativeResponse=null,catalogFailure=false}={}){
 return [
  "const fs=require('node:fs');",
  "global.fetch=async (url,options={})=>{url=String(url);",
  "const capture="+JSON.stringify(captureFile)+";",
  "if(url.startsWith('http://127.0.0.1:9')&&url.includes('/v1/models')){if(!fs.existsSync(capture))fs.writeFileSync(capture,'[]');const rows=JSON.parse(fs.readFileSync(capture));rows.push({kind:'native-models',auth:options.headers&&options.headers.get?options.headers.get('authorization'):null,account:options.headers&&options.headers.get?options.headers.get('chatgpt-account-id'):null});fs.writeFileSync(capture,JSON.stringify(rows));"+nativeResponse+"}",
  "if(url.includes('view=client')){if("+catalogFailure+")throw Error('catalog offline');return Response.json({catalogVersion:'v1',refreshAfterSeconds:900,data:[{id:'flash',minimumPlan:'individual-go',reasoningEfforts:['high'],name:'Flash',context_length:100000,inputModalities:['text']}]});}",
  "if(url.endsWith('/whoami')){if("+catalogFailure+")throw Error('catalog offline');return Response.json({});}",
  "if(url.endsWith('/models')){if("+catalogFailure+")throw Error('catalog offline');return Response.json({data:[{id:'flash',supported_endpoints:['/responses']}]});}",
  "if(url.includes('subscriptions')){if("+catalogFailure+")throw Error('catalog offline');return Response.json({data:{status:'active',planId:'individual-goat'}});}",
  "if(url.includes('credits')){if("+catalogFailure+")throw Error('catalog offline');return Response.json({credits:{purchasedCredits:0,freeCredits:0}});}",
  "if(url.endsWith('/provider/v1/responses')){if(!fs.existsSync(capture))fs.writeFileSync(capture,'[]');const rows=JSON.parse(fs.readFileSync(capture));rows.push({kind:'inference',url,auth:options.headers&&options.headers.get?options.headers.get('authorization'):null,cache:options.headers&&options.headers.get?options.headers.get('chatgpt-account-id'):null,body:options.body?JSON.parse(options.body):null});fs.writeFileSync(capture,JSON.stringify(rows));"+inferenceResponse+"}",
  "throw Error('Unexpected fetch '+url);};"
 ];
}

test('router rejects unauthenticated requests before contacting an upstream',async t=>{
 const probe=[];
 const router=await startRouter(t,[
  "const fs=require('node:fs');",
  "global.fetch=async()=>{fs.writeFileSync("+JSON.stringify(path.join(os.tmpdir(),'cmdc-router-unauth-called'))+",'called');return Response.json({});};"
 ]);
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/models');
 assert.equal(response.status,401);
 assert.match(await response.text(),/authentication required/i);
});
test('missing Codex auth is a 401 rather than an internal proxy error',async t=>{
 const router=await startRouter(t,["global.fetch=async()=>Response.json({});"],'http://127.0.0.1:9/v1',{noAuth:true});
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/models',{headers:{Authorization:'Bearer anything'}});
 assert.equal(response.status,401);
});

test('malformed JSON is a 400 and unsupported content encoding is a 415',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-bad-request-'));
 const router=await startRouter(t,["global.fetch=async()=>Response.json({id:'unexpected'});"]);
 const authorization={Authorization:'Bearer '+router.codexToken};
 const malformed=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers:{...authorization,'Content-Type':'application/json'},body:'{broken'});
 assert.equal(malformed.status,400);
 const unsupported=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers:{...authorization,'Content-Type':'application/json','Content-Encoding':'snappy'},body:'{}'});
 assert.equal(unsupported.status,415);
});

test('native inference preserves Codex credentials and streams the upstream body',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-native-capture-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,[
  "const fs=require('node:fs');",
  "global.fetch=async (url,options={})=>{fs.writeFileSync("+JSON.stringify(capture)+",JSON.stringify({url,authorization:options.headers.get('authorization'),account:options.headers.get('chatgpt-account-id'),body:JSON.parse(options.body)}));",
  "const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: first\\n\\n'));controller.enqueue(new TextEncoder().encode('data: second\\n\\n'));controller.close();}});",
  "return new Response(stream,{status:200,headers:{'content-type':'text/event-stream','x-upstream':'native'}});};"
 ]);
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+router.codexToken,'ChatGPT-Account-ID':'account-fixture'},body:JSON.stringify({model:'gpt-native',input:'hello',stream:true})});
 assert.equal(response.status,200);
 assert.equal(response.headers.get('x-upstream'),'native');
 assert.equal(await response.text(),'data: first\n\ndata: second\n\n');
 const sent=JSON.parse(fs.readFileSync(capture));
 assert.equal(sent.url,'http://127.0.0.1:9/v1/responses');
 assert.equal(sent.authorization,'Bearer '+router.codexToken);
 assert.equal(sent.account,'account-fixture');
 assert.equal(sent.body.model,'gpt-native');
});

test('model list merges native and CommandCode catalogs and disables HTTP caching',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-model-list-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{
  nativeResponse:"return Response.json({models:[{slug:'gpt-native',display_name:'Native'}]});"
 }));
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/models?client_version=0.166.0',{headers:{Authorization:'Bearer '+router.codexToken}});
 assert.equal(response.status,200);
 assert.equal(response.headers.get('cache-control'),'no-store');
 const data=await response.json();
 assert.deepEqual(data.models.map(model=>model.slug),['gpt-native','commandcode/flash']);
 const calls=JSON.parse(fs.readFileSync(capture));
 assert.equal(calls.find(call=>call.kind==='native-models').auth,'Bearer '+router.codexToken);
});

test('CommandCode catalog failure leaves native models available',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-native-fallback-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{
  catalogFailure:true,
  nativeResponse:"return Response.json({models:[{slug:'gpt-native',display_name:'Native'}]});"
 }));
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/models',{headers:{Authorization:'Bearer '+router.codexToken}});
 assert.equal(response.status,200);
 assert.equal(response.headers.get('cache-control'),'no-store');
 assert.deepEqual((await response.json()).models.map(model=>model.slug),['gpt-native']);
});

test('unknown models and unsupported efforts are rejected before inference',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-validation-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{inferenceResponse:"return Response.json({id:'unexpected'});"}));
 const headers={'Content-Type':'application/json',Authorization:'Bearer '+router.codexToken};
 const unknown=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers,body:JSON.stringify({model:'commandcode/unknown',input:'hello',stream:false})});
 assert.equal(unknown.status,400);
 assert.match(await unknown.text(),/Unknown CommandCode model/);
 const effort=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers,body:JSON.stringify({model:'commandcode/flash',reasoning:{effort:'low'},input:'hello',stream:false})});
 assert.equal(effort.status,400);
 assert.match(await effort.text(),/Unsupported effort/);
 assert.equal(fs.existsSync(capture)?JSON.parse(fs.readFileSync(capture)).some(call=>call.kind==='inference'):false,false);
});

test('compressed CommandCode requests are decoded and streaming responses pass through',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-compressed-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{
  inferenceResponse:"const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: chunk-one\\n\\n'));controller.enqueue(new TextEncoder().encode('data: [DONE]\\n\\n'));controller.close();}});return new Response(stream,{status:200,headers:{'content-type':'text/event-stream','x-request-id':'request-fixture'}});"
 }));
 const payload=zlib.gzipSync(Buffer.from(JSON.stringify({model:'commandcode/flash',reasoning:{effort:'high'},prompt_cache_key:'private-cache-fixture',input:'hello',stream:true})));
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers:{'Content-Type':'application/json','Content-Encoding':'gzip',Authorization:'Bearer '+router.codexToken,'ChatGPT-Account-ID':'must-not-forward'},body:payload});
 assert.equal(response.status,200);
 assert.equal(response.headers.get('x-request-id'),'request-fixture');
 assert.equal(await response.text(),'data: chunk-one\n\ndata: [DONE]\n\n');
 const inference=JSON.parse(fs.readFileSync(capture)).find(call=>call.kind==='inference');
 assert.equal(inference.body.model,'flash');
 assert.equal(inference.body.prompt_cache_key,'private-cache-fixture');
 assert.equal(inference.auth,'Bearer '+router.commandKey);
 assert.equal(inference.cache,null);
});

test('CommandCode upstream errors pass through with their status and body',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-upstream-error-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{
  inferenceResponse:"return Response.json({error:{message:'rate limited'}},{status:429,headers:{'x-upstream-error':'limited'}});"
 }));
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+router.codexToken},body:JSON.stringify({model:'commandcode/flash',reasoning:{effort:'high'},input:'hello',stream:false})});
 assert.equal(response.status,429);
 assert.equal(response.headers.get('x-upstream-error'),'limited');
 assert.deepEqual(await response.json(),{error:{message:'rate limited'}});
});
test('authenticated refresh forces catalog discovery',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-refresh-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{}));
 const response=await fetch('http://127.0.0.1:'+router.port+'/refresh',{method:'POST',headers:{Authorization:'Bearer '+router.codexToken}});
 assert.equal(response.status,200);
 assert.equal((await response.json()).count,1);
});
test('diagnostic log write failure cannot break inference',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-log-failure-')),capture=path.join(dir,'capture.json');
 const router=await startRouter(t,catalogMockLines(capture,{inferenceResponse:"return Response.json({id:'still-works'});"}));
 fs.mkdirSync(path.join(router.dir,'requests.jsonl'));
 const response=await fetch('http://127.0.0.1:'+router.port+'/v1/responses',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+router.codexToken},body:JSON.stringify({model:'commandcode/flash',reasoning:{effort:'high'},input:'hello',stream:false})});
 assert.equal(response.status,200);
 assert.deepEqual(await response.json(),{id:'still-works'});
});
