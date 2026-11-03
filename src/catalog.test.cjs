const {test}=require('node:test');
const assert=require('node:assert/strict');
const {build,createCatalog,getKey}=require('./catalog.cjs');
const model=(id,minimumPlan,reasoningEfforts)=>({id,minimumPlan,reasoningEfforts,name:id,context_length:1000000,inputModalities:['text']});
test('API efforts, plan changes, credits and endpoint changes drive catalog',()=>{
 const client={catalogVersion:'v1',refreshAfterSeconds:900,data:[model('flash','individual-go',['off','high','max']),model('premium','individual-max',['low','high'])]};
 const pub={data:client.data.map(m=>({id:m.id,supported_endpoints:['/responses']}))};
 const sub={data:{status:'active',planId:'individual-goat'}};
 const credits={credits:{purchasedCredits:0,freeCredits:0}};
 let result=build(client,pub,sub,credits,{});
 assert.deepEqual(result.models.map(m=>m.slug),['commandcode/flash']);
 assert.deepEqual(result.models[0].supported_reasoning_levels.map(x=>x.effort),['none','high','max']);
 assert.equal(result.models[0].supports_reasoning_effort_updates,false);
 client.data[0].reasoningEfforts=['low','max'];
 assert.deepEqual(build(client,pub,sub,credits,{}).models[0].supported_reasoning_levels.map(x=>x.effort),['low','max']);
 sub.data.planId='individual-max';
 assert.equal(build(client,pub,sub,credits,{}).models.length,2);
 sub.data.planId='individual-goat';credits.credits.purchasedCredits=1;
 assert.equal(build(client,pub,sub,credits,{}).models.length,2);
 pub.data[1].supported_endpoints=['/messages'];
 assert.equal(build(client,pub,sub,credits,{}).models.length,1);
});
test('refresh honors server TTL, changed key and forced refresh', async()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-test-'));
 const originalHome=os.homedir, originalFetch=global.fetch, originalNow=Date.now;
 let time=originalNow(),calls=0,efforts=['high'];
 os.homedir=()=>dir;Date.now=()=>time;
 fs.mkdirSync(path.join(dir,'.commandcode'));
 const auth=path.join(dir,'.commandcode/auth.json');
 fs.writeFileSync(auth,JSON.stringify({apiKey:'test-key-one'}));
 global.fetch=async url=>{calls++;return {ok:true,json:async()=>{
   if(url.endsWith('/whoami'))return {};
   if(url.includes('view=client'))return {catalogVersion:'test',refreshAfterSeconds:900,data:[model('flash','individual-go',efforts)]};
   if(url.endsWith('/models'))return {data:[{id:'flash',supported_endpoints:['/responses']}]};
   if(url.includes('subscriptions'))return {data:{status:'active',planId:'individual-goat'}};
   return {credits:{purchasedCredits:0,freeCredits:0}};
 }}};
 try {
   const manager=require('./catalog.cjs').createCatalog(dir,{});
   await manager.refresh();assert.equal(calls,5);
   await manager.refresh();assert.equal(calls,5);
   efforts=['low','max'];time+=901000;
   assert.deepEqual((await manager.refresh()).models[0].supported_reasoning_levels.map(x=>x.effort),efforts);
   assert.equal(calls,10);
   fs.writeFileSync(auth,JSON.stringify({apiKey:'test-key-two'}));
   await manager.refresh();assert.equal(calls,15);
   await manager.refresh(true);assert.equal(calls,20);
 } finally {os.homedir=originalHome;global.fetch=originalFetch;Date.now=originalNow;fs.rmSync(dir,{recursive:true,force:true});}
});
test('detects standard CommandCode auth paths without relying on the legacy home path',()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-key-path-'));
 const originalHome=os.homedir,originalAppData=process.env.APPDATA,originalLocalAppData=process.env.LOCALAPPDATA,originalXdg=process.env.XDG_CONFIG_HOME;
 os.homedir=()=>path.join(dir,'home');
 let auth;
 if(process.platform==='win32'){
  process.env.APPDATA=path.join(dir,'appdata');auth=path.join(process.env.APPDATA,'commandcode','auth.json');
 }else if(process.platform==='darwin'){
  auth=path.join(dir,'home','Library','Application Support','commandcode','auth.json');
 }else{
  process.env.XDG_CONFIG_HOME=path.join(dir,'xdg');auth=path.join(process.env.XDG_CONFIG_HOME,'commandcode','auth.json');
 }
 fs.mkdirSync(path.dirname(auth),{recursive:true});fs.writeFileSync(auth,JSON.stringify({apiKey:'alternate-path-key'}));
 try {
  assert.equal(getKey({keyFile:path.join(dir,'missing-credentials.json')}),'alternate-path-key');
 } finally {
  os.homedir=originalHome;
  if(originalAppData===undefined)delete process.env.APPDATA;else process.env.APPDATA=originalAppData;
  if(originalLocalAppData===undefined)delete process.env.LOCALAPPDATA;else process.env.LOCALAPPDATA=originalLocalAppData;
  if(originalXdg===undefined)delete process.env.XDG_CONFIG_HOME;else process.env.XDG_CONFIG_HOME=originalXdg;
  fs.rmSync(dir,{recursive:true,force:true});
 }
});
test('persisted catalog is reused for the same key after restart when refresh fails',async()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-persist-cache-'));
 const originalHome=os.homedir,originalFetch=global.fetch,originalNow=Date.now;
 let time=originalNow(),fail=false;
 os.homedir=()=>dir;Date.now=()=>time;
 fs.mkdirSync(path.join(dir,'.commandcode'));
 fs.writeFileSync(path.join(dir,'.commandcode/auth.json'),JSON.stringify({apiKey:'persist-key'}));
 global.fetch=async url=>{
  if(fail)throw Error('offline');
  return {ok:true,json:async()=>{
   if(url.endsWith('/whoami'))return {};
   if(url.includes('view=client'))return {catalogVersion:'persist-v1',refreshAfterSeconds:900,data:[model('flash','individual-go',['high'])]};
   if(url.endsWith('/models'))return {data:[{id:'flash',supported_endpoints:['/responses']}]};
   if(url.includes('subscriptions'))return {data:{status:'active',planId:'individual-goat'}};
   return {credits:{purchasedCredits:0,freeCredits:0}};
  }};
 };
 try {
  const first=createCatalog(dir,{});await first.refresh();
  fail=true;time+=901000;
  const restarted=createCatalog(dir,{});
  assert.equal(restarted.status().count,1);
  const reused=await restarted.refresh();
  assert.deepEqual(reused.models.map(m=>m.slug),['commandcode/flash']);
  assert.equal(reused.catalogVersion,'persist-v1');
 } finally {
  os.homedir=originalHome;global.fetch=originalFetch;Date.now=originalNow;
  fs.rmSync(dir,{recursive:true,force:true});
 }
});
test('persisted catalog is not reused after the API key changes',async()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-persist-auth-change-'));
 const originalHome=os.homedir,originalFetch=global.fetch,originalNow=Date.now;
 let time=originalNow(),fail=false;
 os.homedir=()=>dir;Date.now=()=>time;
 fs.mkdirSync(path.join(dir,'.commandcode'));
 const auth=path.join(dir,'.commandcode/auth.json');
 fs.writeFileSync(auth,JSON.stringify({apiKey:'persist-key-one'}));
 global.fetch=async url=>{
  if(fail)throw Error('offline');
  return {ok:true,json:async()=>{
   if(url.endsWith('/whoami'))return {};
   if(url.includes('view=client'))return {catalogVersion:'persist-v1',refreshAfterSeconds:900,data:[model('flash','individual-go',['high'])]};
   if(url.endsWith('/models'))return {data:[{id:'flash',supported_endpoints:['/responses']}]};
   if(url.includes('subscriptions'))return {data:{status:'active',planId:'individual-goat'}};
   return {credits:{purchasedCredits:0,freeCredits:0}};
  }};
 };
 try {
  await createCatalog(dir,{}).refresh();
  fs.writeFileSync(auth,JSON.stringify({apiKey:'persist-key-two'}));
  fail=true;time+=901000;
  await assert.rejects(createCatalog(dir,{}).refresh(),/offline/);
 } finally {
  os.homedir=originalHome;global.fetch=originalFetch;Date.now=originalNow;
  fs.rmSync(dir,{recursive:true,force:true});
 }
});
test('key lookup supports environment, saved credentials, and a clear missing-key error',()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-key-fallback-'));
 const originalEnvironment=process.env.COMMANDCODE_API_KEY,originalHome=os.homedir;
 os.homedir=()=>path.join(dir,'home');
 const saved=path.join(dir,'credentials.json');
 fs.writeFileSync(saved,JSON.stringify({apiKey:'saved-key'}));
 try {
  process.env.COMMANDCODE_API_KEY='environment-key';
  assert.equal(getKey({keyFile:saved}),'environment-key');
  delete process.env.COMMANDCODE_API_KEY;
  assert.equal(getKey({keyFile:saved}),'saved-key');
  fs.writeFileSync(saved,JSON.stringify({}));
  assert.throws(()=>getKey({keyFile:saved}),/API key not found/);
 } finally {
  if(originalEnvironment===undefined)delete process.env.COMMANDCODE_API_KEY;else process.env.COMMANDCODE_API_KEY=originalEnvironment;
  os.homedir=originalHome;fs.rmSync(dir,{recursive:true,force:true});
 }
});
test('catalog entitlement follows status, plans, credits, visibility, effort, and endpoint support',()=>{
 const model=(id,minimumPlan,reasoningEfforts=['high'],extra={})=>({id,minimumPlan,reasoningEfforts,name:id,context_length:100000,inputModalities:['text'],...extra});
 const client={catalogVersion:'v1',refreshAfterSeconds:900,data:[model('flash','individual-go'),model('premium','individual-max')]};
 const pub={data:client.data.map(m=>({id:m.id,supported_endpoints:['/responses']}))};
 const active={data:{status:'active',planId:'individual-goat'}};
 const noCredits={credits:{purchasedCredits:0,freeCredits:0}};
 assert.deepEqual(build(client,pub,active,noCredits,{}).models.map(m=>m.slug),['commandcode/flash']);
 assert.deepEqual(build(client,pub,{data:{status:'trialing',planId:'individual-max'}},noCredits,{}).models.map(m=>m.slug),['commandcode/flash','commandcode/premium']);
 assert.deepEqual(build(client,pub,{data:{status:'past_due',planId:'individual-max'}},noCredits,{}).models.map(m=>m.slug),['commandcode/flash','commandcode/premium']);
 assert.throws(()=>build(client,pub,{data:{status:'canceled',planId:'individual-max'}},noCredits,{}),/No entitled/);
 assert.throws(()=>build(client,pub,{data:{status:'active',planId:'future-plan'}},noCredits,{}),/No entitled/);
 assert.deepEqual(build(client,pub,active,{credits:{purchasedCredits:0,freeCredits:1}},{}).models.map(m=>m.slug),['commandcode/flash','commandcode/premium']);
 client.data[0].hidden=true;
 assert.deepEqual(build(client,pub,active,{credits:{purchasedCredits:1,freeCredits:0}},{}).models.map(m=>m.slug),['commandcode/premium']);
 client.data[0].hidden=false;client.data[0].reasoningEfforts=['unsupported-remote-effort'];
 assert.throws(()=>build(client,pub,active,noCredits,{}),/Unknown remote reasoning effort/);
 assert.throws(()=>build({refreshAfterSeconds:1},pub,active,noCredits,{}),/Invalid remote model catalog/);
});
