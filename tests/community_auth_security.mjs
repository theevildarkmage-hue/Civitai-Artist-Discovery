// Exact production factory; all tokens/provider/SQL responses are synthetic.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
let projectCalls=0, writes=0, meScope=33, meId=7, invalid=false, keys=[],userinfoCalls=0;
globalThis.Deno={env:{get:name=>({SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled',DISCOVERY_REGISTRY_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2'}[name])}};
const buckets=new Map();
const transport=async(url,options)=>{
 if(url.includes('/rpc/')) {
  const name=url.split('/').at(-1),args=JSON.parse(options.body);
  if(name==='community_take_rate') {
   keys.push(args.p_key);if(args.p_key==='project:community')projectCalls++;
   const n=(buckets.get(args.p_key)||0)+1;
   if(n>30)return Response.json({message:'RATE_LIMITED'},{status:400});
   buckets.set(args.p_key,n);return new Response(null,{status:204});
  }
  if(name==='community_access')return Response.json({invited:true});
  if(name==='community_quota')return Response.json({used:0,remaining:3,limit:3});
  if(name==='community_registry_upsert'){writes++;return Response.json({apiVersion:1,registered:true});}
  throw Error('Unexpected SQL operation');
 }
 if(url.endsWith('/userinfo')) {userinfoCalls++;return invalid?Response.json({},{status:401}):Response.json({sub:'7',id:7,username:'Verified'});}
 if(url==='https://civitai.com/api/v1/me')return Response.json({id:meId,username:'Verified',tokenScope:meScope,subject:{type:'oauth'}});
 throw Error('Unexpected network');
};
const handler=createCommunityHandler({fetch:transport,invitationOnly:true,distributedLimit:true});
async function request(token,operation='status',extra={},headers={}) {
 const r=await handler(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',...headers},body:JSON.stringify({apiVersion:1,operation,...extra})}));
 return {status:r.status,...await r.json()};
}
invalid=true;
for(let n=0;n<60;n++)assert.equal((await request('invalid-'+n)).code,'AUTH_EXPIRED');
invalid=false;assert.equal((await request('valid-after-flood')).status,200);assert.equal(projectCalls,0);
invalid=true;
for(let n=0;n<35;n++)assert.equal((await request('repeated-invalid')).code,n<30?'AUTH_EXPIRED':'RATE_LIMITED');
invalid=false;assert.equal((await request('different-valid')).status,200);
// Filling bounded ingress bookkeeping must not impose a global lockout either.
invalid=true;for(let n=0;n<2050;n++)await request('map-fill-'+n);
invalid=false;assert.equal((await request('valid-after-map-fill')).status,200);
// Broad, absent, stringly typed, or mismatched provider grants cannot register.
for(const scope of [917539,undefined,'33',1,33554431]) {
 meScope=scope;assert.equal((await request('scope-'+String(scope),'register',{appVersion:'3.0.0-beta.4',platform:'Windows'})).code,'AUTH_EXPIRED');
}
assert.equal(writes,0);meScope=33;meId=8;assert.equal((await request('wrong-me-id')).code,'AUTH_EXPIRED');
meId=7;assert.equal((await request('valid-read','register',{appVersion:'3.0.0-beta.4',platform:'Windows'})).registered,true);assert.equal(writes,1);
assert.equal((await request('forged-scope','register',{appVersion:'3.0.0-beta.4',platform:'Windows',scope:33})).code,'INVALID_REQUEST');
assert.equal((await request('browser','status',{}, {Origin:'https://untrusted.invalid'})).code,'FORBIDDEN');
console.log(JSON.stringify({passed:true,invalidDistinctRequests:60,repeatedInvalidRequests:35,ingressMapFillRequests:2050,validUsersAfterEach:3,globalProjectCharges:projectCalls,wideOrMissingScopeRejected:5,scopeMismatchRejected:true,readOnlyRegistryAccepted:true,hostedNetworkRequests:0}));
