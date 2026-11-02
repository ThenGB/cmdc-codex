const {test}=require('node:test');
const assert=require('node:assert/strict');
const {build}=require('./catalog.cjs');
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
