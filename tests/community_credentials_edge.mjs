import {identityTransport} from './community_identity_transport.mjs';
// Transport contract only: synthetic keys, no hosted credential retrieval.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
const modern='sb_secret_'+crypto.randomUUID().replaceAll('-','');
let configuration,legacy='synthetic-legacy',calls=[];
globalThis.Deno={env:{get:name=>({SUPABASE_URL:'https://owned.invalid',SUPABASE_SECRET_KEYS:configuration,
  SUPABASE_SERVICE_ROLE_KEY:legacy,COMMUNITY_BETA_STATE:'enabled'}[name])}};
const handler=createCommunityHandler({fetch:identityTransport(async(url,options)=>{
  if(url.includes('/rest/v1/rpc/')) {
    calls.push(options.headers);
    if(url.endsWith('community_take_rate'))return new Response(null,{status:204});
    return Response.json({used:0,remaining:3});
  }
  assert.equal(url,'https://auth.civitai.com/api/auth/oauth/userinfo');
  assert.equal(options.headers.Authorization,'Bearer synthetic-civitai');
  return Response.json({sub:'7',id:7,username:'Synthetic'});
})});
const call=()=>handler(new Request('https://owned.invalid',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer synthetic-civitai'},body:JSON.stringify({apiVersion:1,operation:'status'})}));
configuration=JSON.stringify({default:modern});assert.equal((await call()).status,200);
assert.ok(calls.length>0);assert.ok(calls.every(h=>h.apikey===modern && !('Authorization' in h)));
for(const bad of ['{','[]','null','{}',JSON.stringify({default:'not-a-secret-key'})]) {
  configuration=bad;calls=[];assert.equal((await call()).status,503);assert.equal(calls.length,0);
}
configuration=undefined;calls=[];assert.equal((await call()).status,200);
assert.ok(calls.every(h=>h.apikey===legacy && h.Authorization==='Bearer '+legacy));
legacy=undefined;calls=[];assert.equal((await call()).status,503);assert.equal(calls.length,0);
console.log('PASS: modern apikey-only transport preferred; malformed key dictionary fails closed; explicit legacy compatibility; absent credentials denied');
