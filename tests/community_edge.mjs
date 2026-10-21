// Node 24: node --experimental-transform-types tests/community_edge.mjs
// Executes the real Edge handler with isolated upstream/DB transport fixtures.
import assert from 'node:assert/strict';
let handler, fault = '', calls = [], commits = [];
globalThis.Deno = {env:{get:n=>({SUPABASE_URL:'https://test.supabase.co',SUPABASE_SERVICE_ROLE_KEY:'synthetic-server-only'}[n])},
  serve:fn=>{handler=fn;}};
globalThis.fetch = async (url, options={}) => {
  calls.push({url, options});
  const response=(data,status=200)=>new Response(JSON.stringify(data),{status});
  if(url.includes('/rest/v1/rpc/')) {
    const name=url.split('/').at(-1), args=JSON.parse(options.body);
    if(name==='community_take_rate') return fault==='rate-limited' ? response({message:'RATE_LIMITED'},400) : response(null);
    if(name==='community_quota') return response({used:0,remaining:3,limit:3});
    if(name==='community_submit' || name==='community_submit_once') {
      assert.equal(args.p_member,fault==='second-member'?8:7);assert.equal(args.p_creator,42);assert.equal(args.p_image,123);
      commits.push(args); return response({apiVersion:1,imageId:123,quota:{used:args.p_preview?0:1,limit:3}});
    }
    throw Error('Unexpected RPC');
  }
  if(url.includes('/userinfo')) {
    if(['expired','revoked'].includes(fault)) return response({},401);
    if(fault==='timeout') throw new DOMException('synthetic-timeout','TimeoutError');
    if(fault==='second-member') return response({sub:'8',id:8,username:'OtherMember'});
    return response({sub:'7',id:fault==='identity-mismatch'?9:7,username:'Member'});
  }
  // Only fixed official API hosts receive the user's OAuth bearer.
  assert.ok(url.startsWith('https://civitai.red/api/'));
  assert.equal(options.headers.Authorization,'Bearer synthetic-test-token');
  if(url.includes('/api/v1/images')) return response({items:[{id:fault==='mismatch'?124:123,
    type:fault==='video'?'video':'image',username:'Artist',browsingLevel:8,url:'https://image.civitai.com/test/art.jpeg'}]});
  if(url.includes('/api/trpc/image.get')) return response({result:{data:{json:{id:123,
    user:fault==='missing-owner'?{}:{id:42,username:'Artist',deletedAt:null},
    availability:fault==='unlisted'?'Unsearchable':'Public',ingestion:fault==='unscanned'?'Pending':'Scanned',needsReview:fault==='review'?'review':null,
    publishedAt:fault==='future'?'2999-01-01T00:00:00Z':'2026-01-01T00:00:00Z',nsfwLevel:fault==='rating-mismatch'?1:8}}}});
  throw Error('Unexpected network destination');
};
await import('../supabase/functions/community-v1/index.ts');
async function request(body, token='synthetic-test-token') {
  const response=await handler(new Request('https://test.supabase.co/functions/v1/community-v1',{
    method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},
    body:JSON.stringify({apiVersion:1,...body})}));
  return {status:response.status,body:await response.json()};
}
assert.equal((await request({operation:'status'})).body.member.id,7);
let result=await request({operation:'verify',artwork:'https://civitai.red/images/123'});
assert.equal(result.status,200);assert.equal(commits.at(-1).p_preview,true);
result=await request({operation:'recommend',artwork:123});assert.equal(result.status,200);
assert.equal(commits.at(-1).p_preview,false);
assert.ok(!JSON.stringify(result.body).includes('token'));
result=await request({operation:'recommend',artwork:123,idempotencyKey:'synthetic-request-0001'});assert.equal(result.status,200);assert.equal(commits.at(-1).p_key,'synthetic-request-0001');
result=await request({operation:'recommend',artwork:123,idempotencyKey:'bad'});assert.equal(result.body.code,'INVALID_REQUEST');
for(const value of ['https://evil.test/images/123','https://civitai.red@127.0.0.1/images/123','01',true]) {
  calls=[];result=await request({operation:'verify',artwork:value});
  assert.equal(result.body.code,'INVALID_ARTWORK');
  assert.ok(calls.every(c=>!c.url.includes('/api/v1/images')));
}
for(const [scenario,code] of [['expired','AUTH_EXPIRED'],['revoked','AUTH_EXPIRED'],['timeout','UNAVAILABLE'],
  ['rate-limited','RATE_LIMITED'],['video','INVALID_ARTWORK'],['unscanned','INVALID_ARTWORK'],
  ['review','INVALID_ARTWORK'],['future','INVALID_ARTWORK'],['rating-mismatch','INVALID_ARTWORK'],
  ['identity-mismatch','AUTH_EXPIRED'],['mismatch','INVALID_ARTWORK'],
  ['missing-owner','UNVERIFIED_OWNER'],['unlisted','INVALID_ARTWORK']]) {
  fault=scenario; const count=commits.length;
  result=await request({operation:'recommend',artwork:123});
  assert.equal(result.body.code,code);assert.equal(commits.length,count);
}
fault='';
fault='second-member';
assert.equal((await request({operation:'status'})).body.member.id,8);
assert.equal((await request({operation:'recommend',artwork:123})).status,200);
assert.equal(commits.at(-1).p_member,8);
fault='';
result=await request({operation:'recommend',artwork:123,memberId:99,creatorId:88});
assert.equal(result.body.code,'INVALID_REQUEST');
result=await request({operation:'status'},'');assert.equal(result.status,401);
console.log('PASS: Edge identity/owner authority, request routing, preview/commit, failures, URL rejection, fixed-host authenticated verification');
