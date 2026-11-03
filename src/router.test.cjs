const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net'),cp=require('node:child_process');
test('prompt cache metadata passes through without secrets entering the request log',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-cache-test-'));
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
 for(const file of ['router.cjs','catalog.cjs','multimodal.cjs'])fs.copyFileSync(path.join(__dirname,file),path.join(dir,file));
 const codexHome=path.join(dir,'codex-home'),commandHome=path.join(dir,'commandcode-home');
 fs.mkdirSync(codexHome);fs.mkdirSync(commandHome,{recursive:true});
 const codexToken='test-codex-session-only',commandKey='test-commandcode-key-only',cacheKey='test-private-cache-key';
 fs.writeFileSync(path.join(codexHome,'auth.json'),JSON.stringify({tokens:{access_token:codexToken}}));
 fs.writeFileSync(path.join(dir,'settings.json'),JSON.stringify({port,nativeBase:'http://127.0.0.1:1/v1',keyFile:path.join(commandHome,'credentials.json')}));
 fs.writeFileSync(path.join(commandHome,'credentials.json'),JSON.stringify({apiKey:commandKey}));
 const mock=[
  "const fs=require('node:fs'),path=require('node:path');",
  "global.fetch=async (url,options={})=>{url=String(url);",
  "if(url.includes('view=client'))return Response.json({catalogVersion:'test',refreshAfterSeconds:900,data:[{id:'flash',minimumPlan:'individual-go',reasoningEfforts:['high'],name:'Flash',context_length:100000,inputModalities:['text']}]});",
  "if(url.endsWith('/whoami'))return Response.json({});",
  "if(url.endsWith('/models'))return Response.json({data:[{id:'flash',supported_endpoints:['/responses']}]});",
  "if(url.includes('subscriptions'))return Response.json({data:{status:'active',planId:'individual-goat'}});",
  "if(url.includes('credits'))return Response.json({credits:{purchasedCredits:0,freeCredits:0}});",
  "if(url.endsWith('/provider/v1/responses')){fs.writeFileSync(path.join("+JSON.stringify(dir)+",'capture.json'),JSON.stringify({url,body:JSON.parse(options.body)}));return Response.json({id:'resp_cache',usage:{input_tokens:400,input_tokens_details:{cached_tokens:321}}});}",
  "throw Error('Unexpected fetch '+url);};"
 ].join('');
 fs.writeFileSync(path.join(dir,'mock-fetch.cjs'),mock);
 const child=cp.spawn(process.execPath,[path.join(dir,'router.cjs')],{env:{...process.env,CODEX_HOME:codexHome,USERPROFILE:commandHome,NODE_OPTIONS:'--require '+path.join(dir,'mock-fetch.cjs')},windowsHide:true});
 let stderr='';child.stderr.on('data',d=>stderr+=d);
 try {
  for(let i=0;i<50;i++){try{if((await fetch('http://127.0.0.1:'+port+'/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,50));}
  const response=await fetch('http://127.0.0.1:'+port+'/v1/responses',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+codexToken},body:JSON.stringify({model:'commandcode/flash',reasoning:{effort:'high'},prompt_cache_key:cacheKey,input:'hello',stream:false})});
  const text=await response.text();
  assert.equal(response.status,200,text+'\n'+stderr);
  const result=JSON.parse(text);
  assert.equal(result.usage.input_tokens_details.cached_tokens,321);
  const capture=JSON.parse(fs.readFileSync(path.join(dir,'capture.json'),'utf8'));
  assert.equal(capture.body.model,'flash');
  assert.equal(capture.body.prompt_cache_key,cacheKey);
  const log=fs.readFileSync(path.join(dir,'requests.jsonl'),'utf8');
  assert.match(log,/"promptCacheKeyPresent":true/);
  for(const secret of [cacheKey,codexToken,commandKey])assert.equal(log.includes(secret),false);
 } finally {const closed=new Promise(r=>child.once('close',r));child.kill();await closed;fs.rmSync(dir,{recursive:true,force:true});}
});
test('cancelled Desktop websocket prewarm cannot crash the router',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-upgrade-test-'));
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
 for(const file of ['router.cjs','catalog.cjs','multimodal.cjs'])fs.copyFileSync(path.join(__dirname,file),path.join(dir,file));
 fs.writeFileSync(path.join(dir,'settings.json'),JSON.stringify({port,nativeBase:'http://127.0.0.1:1/v1'}));
 const child=cp.spawn(process.execPath,[path.join(dir,'router.cjs')]);
 let stderr='';child.stderr.on('data',d=>stderr+=d);
 try {
  for(let i=0;i<50;i++){try{if((await fetch(`http://127.0.0.1:${port}/health`)).ok)break;}catch{}await new Promise(r=>setTimeout(r,50));}
  for(let batch=0;batch<20;batch++)await Promise.all(Array.from({length:15},()=>new Promise(resolve=>{
   const socket=net.connect(port,'127.0.0.1',()=>{socket.write('GET /v1/responses HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');setImmediate(()=>socket.resetAndDestroy());});
   socket.on('error',()=>{});socket.on('close',resolve);
  })));
  await new Promise(r=>setTimeout(r,100));
  assert.equal(child.exitCode,null,stderr);
  assert.equal((await fetch(`http://127.0.0.1:${port}/health`)).status,200);
  const response=await new Promise((resolve,reject)=>{const s=net.connect(port,'127.0.0.1',()=>s.write('GET /v1/responses HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n'));let data='';s.on('data',d=>data+=d);s.on('end',()=>resolve(data));s.on('error',reject);});
  assert.match(response,/426 Upgrade Required/);
 } finally {const closed=new Promise(r=>child.once('close',r));child.kill();await closed;fs.rmSync(dir,{recursive:true,force:true});}
});
