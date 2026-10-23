// Real Edge handler with bounded synthetic upstream; native tests cover real SQL.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
let enabled=true,valid=true;const calls=[];
globalThis.Deno={env:{get:k=>({SUPABASE_URL:'https://fixture.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',DISCOVERY_REGISTRY_STATE:enabled?'enabled':'disabled',COMMUNITY_BETA_STATE:'disabled'}[k])}};
const handler=createCommunityHandler({invitationOnly:true,fetch:async(url,options)=>{
 if(url.endsWith('/userinfo'))return Response.json(valid?{sub:'7',id:7,username:'VerifiedName'}:{},{status:valid?200:401});
 const name=url.split('/').at(-1),args=JSON.parse(options.body);calls.push({name,args});
 if(name==='community_take_rate')return new Response(null,{status:204});
 if(name==='community_registry_upsert')return Response.json({apiVersion:1,registered:true});
 throw Error('Unexpected Community RPC');
}});
async function call(extra={},operation='register'){
 const response=await handler(new Request('https://fixture.invalid',{method:'POST',headers:{Authorization:'Bearer synthetic','Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation,...(operation==='register'?{appVersion:'3.0.0-beta.1',platform:'Linux',disclosure:'2026-10-23'}:{}),...extra})}));return response.json();
}
assert.equal((await call()).registered,true);
assert.deepEqual(calls.find(x=>x.name==='community_registry_upsert').args,{p_member:7,p_name:'VerifiedName',p_version:'3.0.0-beta.1',p_platform:'Linux',p_disclosure:'2026-10-23'});
assert.equal(calls.some(x=>x.name==='community_access'),false);
for(const extra of [{memberId:99},{username:'Forged'},{platform:'Windows; forged'},{disclosure:'old'}])assert.equal((await call(extra)).code,'INVALID_REQUEST');
enabled=false;assert.equal((await call()).registered,false);
valid=false;assert.equal((await call()).code,'AUTH_EXPIRED');
valid=true;assert.equal((await call({},'gallery')).code,'BETA_DISABLED');
assert.equal((await call({},'status')).beta.available,false);
console.log(JSON.stringify({passed:true,checks:11,realHandler:true,syntheticTransport:true}));
