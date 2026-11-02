const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const os = require('node:os');
function getKey(settings) {
  const auth = path.join(os.homedir(),'.commandcode','auth.json');
  if (fs.existsSync(auth)) {const value=read(auth).apiKey;if(value)return value;}
  return read(settings.keyFile).apiKey;
}
const read = p => JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const ranks = ['individual-go','individual-go-v1','individual-goat','individual-pro','individual-pro-v1','individual-provider','individual-max','individual-ultra','teams-pro'];
function build(client, publicModels, subscription, credits, template) {
  if (!Array.isArray(client.data) || !Array.isArray(publicModels.data) || !client.refreshAfterSeconds) throw Error('Invalid remote model catalog');
  const plan = ['active','trialing','past_due'].includes(subscription.data?.status) ? subscription.data.planId : null;
  const extra = (credits.credits?.purchasedCredits || 0)+(credits.credits?.freeCredits || 0)>0;
  const endpoints = new Map(publicModels.data.map(x=>[x.id,x.supported_endpoints]));
  const models = client.data.filter(m => endpoints.get(m.id)?.includes('/responses') && !m.hidden && (extra || (plan && ranks.indexOf(plan)>=0 && ranks.indexOf(m.minimumPlan)>=0 && ranks.indexOf(plan)>=ranks.indexOf(m.minimumPlan)))).map((m,i) => {
    const efforts = [...new Set((m.reasoningEfforts || []).map(x=>x==='off'?'none':x))];
    if (!efforts.every(x=>['none','minimal','low','medium','high','xhigh','max','ultra'].includes(x))) throw Error('Unknown remote reasoning effort');
    return {...template,slug:'commandcode/'+m.id,display_name:m.name+' (CommandCode)',description:m.display?.description || m.name,priority:100+i,context_window:m.context_length,input_modalities:m.inputModalities || ['text'],supported_reasoning_levels:efforts.map(effort=>({effort,description:effort==='none'?'Thinking off':'Reasoning: '+effort})),default_reasoning_level:efforts.includes('high')?'high':efforts[0] || null,supports_reasoning_effort_updates:false};
  });
  if (!models.length) throw Error('No entitled Responses models in remote catalog');
  return {models,plan,catalogVersion:client.catalogVersion,refreshAfterSeconds:client.refreshAfterSeconds};
}
function createCatalog(dir, settings) {
  const file = path.join(dir,'commandcode-models.json');
  const template = {base_instructions:'You are Codex, a coding agent. Use the provided tools to complete the user task. Preserve existing work and report verification accurately.',shell_type:'unified_exec',visibility:'list',supported_in_api:true,effective_context_window_percent:95,truncation_policy:{mode:'tokens',limit:10000},experimental_supported_tools:[],support_verbosity:false,apply_patch_tool_type:'freeform',supports_search_tool:false,use_responses_lite:false,tool_mode:'code_mode_only'};
  let state, pending, lastError;
  async function refresh(force=false) {
    const key = getKey(settings);
    const fingerprint = crypto.createHash('sha256').update(key).digest('hex');
    if (!force && state?.fingerprint===fingerprint && Date.now()<state.nextRefresh) return state;
    if (pending) return pending;
    pending = (async()=>{
      const get = async route => { const r=await fetch('https://api.commandcode.ai'+route,{headers:{Authorization:'Bearer '+key},signal:AbortSignal.timeout(20000)}); if(!r.ok)throw Error('Catalog HTTP '+r.status); return r.json(); };
      try {
        const who = await get('/alpha/whoami');
        const query = who.org?.id ? '?orgId='+encodeURIComponent(who.org.id) : '';
        const [client,models,sub,credits] = await Promise.all([get('/provider/v1/models?view=client'),get('/provider/v1/models'),get('/alpha/billing/subscriptions'+query),get('/alpha/billing/credits'+query)]);
        const data = build(client,models,sub,credits,template);
        state={...data,fingerprint,updatedAt:new Date().toISOString(),nextRefresh:Date.now()+data.refreshAfterSeconds*1000};
        fs.writeFileSync(file+'.tmp',JSON.stringify(state,null,2));fs.renameSync(file+'.tmp',file);lastError=null;
        return state;
      } catch(e) {lastError=e.message;if(state?.fingerprint===fingerprint){state.nextRefresh=Date.now()+60000;return state;}throw e;}
      finally {pending=null;}
    })();
    return pending;
  }
  const timer=setInterval(()=>refresh().catch(()=>{}),60000);timer.unref();
  return {refresh,status:()=>({updatedAt:state?.updatedAt,plan:state?.plan,count:state?.models.length,nextRefresh:state?.nextRefresh,lastError})};
}
module.exports={build,createCatalog,getKey};
