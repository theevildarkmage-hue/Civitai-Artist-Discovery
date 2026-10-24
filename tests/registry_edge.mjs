import {identityTransport} from './community_identity_transport.mjs';
// Real Edge handler with bounded synthetic upstream; native tests cover real SQL.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
let enabled=true,valid=true,ownerMode=false,owner='7',until=new Date(Date.now()+600000).toISOString(),registryState;const calls=[];
globalThis.Deno={env:{get:k=>({SUPABASE_URL:'https://fixture.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',DISCOVERY_REGISTRY_STATE:registryState??(enabled?'enabled':'disabled'),COMMUNITY_BETA_STATE:'disabled',COMMUNITY_TEST_OWNER_ID:owner,COMMUNITY_OWNER_TEST_EXPIRES_AT:until}[k])}};
const transport=async(url,options)=>{
 if(url.endsWith('/userinfo'))return Response.json(valid?{sub:'7',id:7,username:'VerifiedName'}:{},{status:valid?200:401});
 const name=url.split('/').at(-1),args=JSON.parse(options.body);calls.push({name,args});
 if(name==='community_take_rate')return new Response(null,{status:204});
 if(name==='community_registry_upsert')return Response.json({apiVersion:1,registered:true});
 throw Error('Unexpected Community RPC');
};
const ordinary=createCommunityHandler({invitationOnly:true,fetch:identityTransport(transport)});
const restricted=createCommunityHandler({invitationOnly:true,ownerOnly:true,fetch:identityTransport(transport)});
async function call(extra={},operation='register'){
 const response=await (ownerMode?restricted:ordinary)(new Request('https://fixture.invalid',{method:'POST',headers:{Authorization:'Bearer synthetic','Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation,...(operation==='register'?{appVersion:'3.0.0-beta.1',platform:'Linux'}:{}),...extra})}));return response.json();
}
assert.equal((await call()).registered,true);
assert.deepEqual(calls.find(x=>x.name==='community_registry_upsert').args,{p_member:7,p_name:'VerifiedName',p_version:'3.0.0-beta.1',p_platform:'Linux'});
assert.equal(calls.some(x=>x.name==='community_access'),false);
for(const extra of [{memberId:99},{username:'Forged'},{platform:'Windows; forged'},{disclosure:'old'}])assert.equal((await call(extra)).code,'INVALID_REQUEST');
enabled=false;assert.equal((await call()).registered,false);
valid=false;assert.equal((await call()).code,'AUTH_EXPIRED');
valid=true;assert.equal((await call({},'gallery')).code,'BETA_DISABLED');
assert.equal((await call({},'status')).beta.available,false);
enabled=true;ownerMode=true;
let writes=calls.filter(c=>c.name==='community_registry_upsert').length;
owner='8';assert.equal((await call()).code,'FORBIDDEN');
assert.equal(calls.filter(c=>c.name==='community_registry_upsert').length,writes);
owner='7';until='2000-01-01T00:00:00Z';assert.equal((await call()).code,'BETA_DISABLED');
until='invalid';assert.equal((await call()).code,'BETA_DISABLED');
until=new Date(Date.now()+3600000).toISOString();assert.equal((await call()).code,'BETA_DISABLED');
assert.equal(calls.filter(c=>c.name==='community_registry_upsert').length,writes);
until=new Date(Date.now()+600000).toISOString();assert.equal((await call()).registered,true);
enabled=false;assert.equal((await call()).registered,false);
registryState='owner_test';ownerMode=false;writes=calls.filter(c=>c.name==='community_registry_upsert').length;
assert.equal((await call()).registered,false);
assert.equal(calls.filter(c=>c.name==='community_registry_upsert').length,writes);
ownerMode=true;assert.equal((await call()).registered,true);
owner='8';writes=calls.filter(c=>c.name==='community_registry_upsert').length;
assert.equal((await call()).code,'FORBIDDEN');
assert.equal(calls.filter(c=>c.name==='community_registry_upsert').length,writes);
owner='7';until='2000-01-01T00:00:00Z';assert.equal((await call()).code,'BETA_DISABLED');
assert.equal(calls.filter(c=>c.name==='community_registry_upsert').length,writes);
registryState='invalid';until=new Date(Date.now()+600000).toISOString();assert.equal((await call()).registered,false);
console.log(JSON.stringify({passed:true,checks:27,realHandler:true,syntheticTransport:true}));
