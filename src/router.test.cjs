const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net'),cp=require('node:child_process');
test('cancelled Desktop websocket prewarm cannot crash the router',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cmdc-upgrade-test-'));
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
 for(const file of ['router.cjs','catalog.cjs'])fs.copyFileSync(path.join(__dirname,file),path.join(dir,file));
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
