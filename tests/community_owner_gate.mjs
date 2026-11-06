import {identityTransport} from './community_identity_transport.mjs';
// Local adversarial transport fixtures; no live identity or database claims.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
let settings={}, member=7, invalid=false, rpc=[];
globalThis.Deno={env:{get:n=>({SUPABASE_URL:'https://fixture.invalid',
  SUPABASE_SERVICE_ROLE_KEY:'synthetic-only',COMMUNITY_ALLOWED_RATINGS:'1,2',...settings}[n])}};
const handler=createCommunityHandler({ownerOnly:true,ipLimit:1000,fetch:identityTransport(async(url,options)=>{
  if(url.includes('/rpc/')) {
    const name=url.split('/').at(-1);rpc.push({name,args:JSON.parse(options.body)});
    if(name==='community_take_rate')return new Response(null,{status:204});
    return Response.json({apiVersion:1,quota:{used:1},items:[]});
  }
  if(url.endsWith('/userinfo'))return invalid?Response.json({}, {status:401}):Response.json({sub:String(member),id:member,username:'Fixture'});
  if(url.includes('/api/v1/images'))return Response.json({items:[{id:123,type:'image',username:'Artist',browsingLevel:1,url:'https://image.civitai.com/test.jpeg'}]});
  if(url.includes('/image.get'))return Response.json({result:{data:{json:{id:123,user:{id:42,username:'Artist'},availability:'Public',ingestion:'Scanned',publishedAt:'2026-01-01T00:00:00Z',nsfwLevel:1}}}});
  throw Error('Unexpected fixture destination');
})});
const valid=()=>({COMMUNITY_BETA_STATE:'owner_test',COMMUNITY_TEST_OWNER_ID:'7',COMMUNITY_OWNER_TEST_EXPIRES_AT:new Date(Date.now()+20*60*1000).toISOString()});
let checks=0;
async function check(expected,{operation='recommend',extra={},headers={},query='',auth=true}={}) {
  rpc=[];
  const response=await handler(new Request('https://fixture.invalid/'+query,{method:'POST',headers:{'Content-Type':'application/json',...(auth?{Authorization:'Bearer synthetic-only'}:{}),...headers},body:JSON.stringify({apiVersion:1,operation,...(['verify','recommend','withdraw'].includes(operation)?{artwork:123}:{}),...extra})}));
  const body=await response.json();assert.equal(body.code??response.status,expected);checks++;
  if(expected!==200)assert.ok(rpc.every(r=>r.name==='community_take_rate'),'Denied request reached content/quota/write RPC');
}
settings=valid();member=8;
for(const operation of ['status','gallery','verify','recommend','withdraw'])await check('FORBIDDEN',{operation});
for(const extra of [{memberId:7},{ownerId:7},{creatorId:7},{sub:7},{ownerOnly:false},{COMMUNITY_TEST_OWNER_ID:'8'}])await check('FORBIDDEN',{extra});
await check('FORBIDDEN',{headers:{'X-Owner-Id':'7','X-Community-Owner-Only':'false'},query:'?ownerOnly=false&memberId=7'});
member=7;
for(const owner of [undefined,'','07','0','-7','7x','9007199254740991']){settings={...valid(),COMMUNITY_TEST_OWNER_ID:owner};await check('BETA_DISABLED');}
for(const state of [undefined,'disabled','enabled','unknown']){settings={...valid(),COMMUNITY_BETA_STATE:state};await check('BETA_DISABLED');}
for(const until of [undefined,'','not-a-date','2020-01-01T00:00:00Z','2999-01-01T00:00:00Z','2026-01-01']){settings={...valid(),COMMUNITY_OWNER_TEST_EXPIRES_AT:until};await check('BETA_DISABLED');}
settings=valid();
for(const extra of [{memberId:7},{ownerId:7},{ownerOnly:false},{submissions:true}])await check('INVALID_REQUEST',{extra});
for(const operation of ['status','gallery','verify','recommend'])await check(200,{operation});
assert.equal(rpc.at(-1).name,'community_submit');assert.equal(rpc.at(-1).args.p_member,7);
settings={...valid(),COMMUNITY_BETA_STATE:'disabled'};await check('BETA_DISABLED',{headers:{'X-Community-Beta-State':'enabled'},query:'?COMMUNITY_BETA_STATE=enabled'});
invalid=true;settings=valid();await check('AUTH_EXPIRED');invalid=false;await check('AUTH_REQUIRED',{auth:false});
console.log(JSON.stringify({passed:true,checks,coverage:'Owner/non-owner, missing/malformed configuration, closed public enabled state, expiry, spoofed body/query/headers, write RPC routing, invalid/missing authentication; transport fixtures only'}));
