// Read-only official-package protocol experiment, no model/network credentials.
// node tools/dsh_subagent_policy_probe.mjs [installed-package-directory] [report]
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const packageRoot=resolve(process.argv[2]??'.codex-temp/dsh-poc');
const require=createRequire(pathToFileURL(resolve(packageRoot,'package.json')));
const load=async name=>import(pathToFileURL(require.resolve(name)).href);
const {Context,Service}=await load('@deepseek-ai/cordis');
const {default:SystemPrompt}=await load('@deepseek-ai/dsh-system-prompt');
const {default:Tools}=await load('@deepseek-ai/dsh-tools');
const {createScope,scopeOf}=await load('@deepseek-ai/dsh-scope');
const {applyChildComposition}=await load('@deepseek-ai/dsh-subagent');
const {default:WorkflowEngine}=await load('@deepseek-ai/dsh-workflow-worker-thread');
const packageVersion=JSON.parse(fs.readFileSync(resolve(packageRoot,'node_modules/@deepseek-ai/dsh/package.json'))).version;
assert.equal(packageVersion,'0.1.5-rc.1');

const ctx = new Context();
await ctx.plugin(SystemPrompt);
await ctx.plugin(Tools);
const register = (c,name) => c.tools.register({name,description:'Synthetic fixture',parameters:{},output:{schema:{type:'object'},render:()=>[{type:'text',text:'fixture'}]},execute:async()=>({})});
const visible = c=>c.tools.wireSchemas(scopeOf(c)).schemas.map(tool=>tool.name).sort();
register(ctx,'global_file');
let scopeResult;
await ctx.inject(['tools','systemPrompt'],async c=>{
  const parent=createScope(c,{}), child=createScope(c,{});
  register(parent.ctx,'mcp__session__echo');
  parent.ctx.tools.restrict({deny:['global_file']});
  applyChildComposition(child.ctx,{ctx:parent.ctx},{});
  scopeResult={parent_tools:visible(parent.ctx),child_tools:visible(child.ctx)};
  await child.dispose();await parent.dispose();
});
const report={checked_at:new Date().toISOString(),package_version:packageVersion,node_version:process.version,
  method:'Official Cordis scopes, tools and child-composition function; workflow real worker threads with synthetic child provider, no model or platform credentials',
  scope:scopeResult,workflow:[]};
report.official_package_sources={};
for(const name of ['dsh-agent-loop','dsh-tools','dsh-scope','dsh-subagent','dsh-subagent-in-process-driver',
  'dsh-subagent-spawn-in-process','dsh-tool-subagent','dsh-workflow-worker-thread','dsh-acp']){
  const directory=resolve(packageRoot,'node_modules/@deepseek-ai',name);
  const metadata=JSON.parse(fs.readFileSync(resolve(directory,'package.json')));
  assert.equal(metadata.version,'0.1.5-rc.1');
  report.official_package_sources[name]={version:metadata.version,repository:metadata.repository,
    npm_url:'https://www.npmjs.com/package/@deepseek-ai/'+name+'/v/'+metadata.version,
    index_sha256:createHash('sha256').update(fs.readFileSync(resolve(directory,'lib/index.js'))).digest('hex')};
}

let active=0,maximum=0,started=0,disposed=0,aborted=0;
const requests=[];
class FixtureSubagents extends Service {
  constructor(c){super(c,'subagents');}
  getProvider(){return {name:'fixture'};}
  async start(provider,request){
    active++;started++;maximum=Math.max(maximum,active);
    requests.push({hasToolFilter:request.toolFilter!==undefined,maxDepth:request.maxDepth??null,signal:!!request.signal});
    let done=false,finish;
    const result=new Promise(resolve=>{
      let timer;
      finish=(stopReason)=>{if(done)return;done=true;clearTimeout(timer);active--;resolve({output:[{type:'text',text:'FIXTURE'}],stopReason});};
      request.signal.addEventListener('abort',()=>{if(!done)aborted++;finish('aborted');},{once:true});
      timer=setTimeout(()=>finish('completed'),120);
    });
    return {id:'synthetic-'+started,result,dispose:async()=>{disposed++;finish('aborted');await result;}};
  }
}
await ctx.plugin(FixtureSubagents);
await ctx.plugin(WorkflowEngine,{provider:'fixture',maxConcurrentAgents:1,maxTotalAgents:3,maxItemsPerCall:8,syncTimeoutMs:250,disposeGraceMs:500});
for(const [kind,script,cancel] of [
  ['bounded','return await parallel([()=>agent("A"),()=>agent("B"),()=>agent("C")]);',false],
  ['total_cap','return await parallel([()=>agent("A"),()=>agent("B"),()=>agent("C"),()=>agent("D")]);',false],
  ['cancel','return await parallel([()=>agent("A"),()=>agent("B")]);',true],
]){
  active=maximum=started=disposed=aborted=0; requests.length=0;
  const controller=new AbortController();
  const run=ctx.workflowEngine.start({meta:{name:'synthetic-'+kind,description:'Offline fixture'},script,parent:{id:'synthetic-root'},signal:controller.signal});
  if(cancel)setTimeout(()=>controller.abort(),75);
  const result=await run.result;await run.dispose();
  report.workflow.push({kind,result,maximum,started,disposed,aborted,active_after_dispose:active,requests:[...requests]});
}
active=maximum=started=disposed=aborted=0;requests.length=0;
const twins=[0,1].map(i=>ctx.workflowEngine.start({meta:{name:'synthetic-twin-'+i,description:'Offline fixture'},script:'return await agent("A");',parent:{id:'synthetic-root'}}));
const twinResults=await Promise.all(twins.map(run=>run.result));await Promise.all(twins.map(run=>run.dispose()));
report.workflow.push({kind:'two_runs_same_parent',results:twinResults,maximum,started,disposed,active_after_dispose:active});
for(const runtime of [...ctx.registry.values()].reverse())for(const fiber of [...runtime.fibers])await fiber.dispose();
assert.deepEqual(report.scope.parent_tools,['mcp__session__echo']);
assert.deepEqual(report.scope.child_tools,['global_file']);
assert.equal(report.workflow[0].maximum,1);
assert.equal(report.workflow[0].started,3);
assert.equal(report.workflow[1].result.stopReason,'error');
assert.equal(report.workflow[2].result.stopReason,'cancelled');
assert.equal(report.workflow[2].active_after_dispose,0);
assert.equal(report.workflow[3].maximum,2);
for(const entry of report.workflow)if(entry.result?.error)entry.result.error=entry.result.error.split('\n')[0];
if(process.argv[3])fs.writeFileSync(resolve(process.argv[3]),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
