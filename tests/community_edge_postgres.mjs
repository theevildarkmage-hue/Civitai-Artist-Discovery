// Real handler + real PostgreSQL RPCs via a narrow test SQL adapter.
// Civitai UserInfo/artwork transport is synthetic; no PostgREST/Deno deployment claim.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {randomInt} from 'node:crypto';
if (process.env.CAD_ALLOW_ISOLATED_DB_TESTS !== '1' || !process.env.CAD_TEST_PG_SYSTEM_IDENTIFIER) {
  console.log('SKIP: run through scripts/validate-postgres.py');
  process.exit(0);
}
assert.equal(process.env.PGHOST,'127.0.0.1');
assert.equal(process.env.PGUSER,'service_role');
assert.match(process.env.PGDATABASE,/^cad3_security_[a-f0-9]+$/);
const execute=promisify(execFile), seed=1_900_000_000+randomInt(1_000_000);
const sqlValue=value=>value===null?'null':typeof value==='boolean'?String(value):
  typeof value==='number'?(assert.ok(Number.isSafeInteger(value)),String(value)):
  "'"+String(value).replaceAll("'","''")+"'";
const signatures={community_take_rate:['p_key'],community_quota:['p_member'],
  community_gallery:['p_cursor','p_limit'],community_withdraw:['p_member','p_image'],
  community_submit:['p_member','p_name','p_image','p_creator','p_preview'],
  community_submit_once:['p_member','p_name','p_image','p_creator','p_key']};
let handler, mode='', rpcCalls=0;
globalThis.Deno={env:{get:name=>({SUPABASE_URL:'https://isolated.invalid',
  COMMUNITY_BETA_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2,4,8,16',SUPABASE_SERVICE_ROLE_KEY:'synthetic-adapter-key'}[name])},serve:fn=>{handler=fn;}};
globalThis.fetch=async(url,options={})=>{
  const reply=(data,status=200)=>new Response(JSON.stringify(data),{status});
  if(url.startsWith('https://isolated.invalid/rest/v1/rpc/')) {
    assert.equal(options.headers.apikey,'synthetic-adapter-key');
    const name=url.split('/').at(-1), args=JSON.parse(options.body), fields=signatures[name];
    assert.ok(fields,'Unknown RPC');assert.deepEqual(Object.keys(args).sort(),[...fields].sort());
    const statement=`select public.${name}(${fields.map(field=>sqlValue(args[field])).join(',')});`;
    rpcCalls++;
    try {
      const {stdout}=await execute('psql',['-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],
        {env:process.env,timeout:15000,windowsHide:true});
      return reply(name==='community_take_rate'?null:JSON.parse(stdout.trim()));
    } catch(error) {
      const code=String(error.stderr).match(/(?:ERROR:\s+)([A-Z_]+)\b/)?.[1]||'INTERNAL';
      return reply({message:code},400);
    }
  }
  const bearer=options.headers?.Authorization?.slice(7)||'';
  if(url==='https://auth.civitai.com/api/auth/oauth/userinfo') {
    if(['expired','revoked'].includes(bearer))return reply({},401);
    if(bearer==='malformed')return reply({sub:'not-an-id',username:'Synthetic'});
    const index=Number(bearer.replace('isolated-member-',''));
    assert.ok(Number.isInteger(index)&&index>=0&&index<10);
    return reply({sub:String(seed+index),id:seed+index,preferred_username:`SyntheticMember${index}`});
  }
  assert.ok(url.startsWith('https://civitai.red/api/'),'Unexpected transport destination');
  if(mode==='unavailable')return reply({},503);
  let image;
  if(url.includes('/api/v1/images?')) {
    image=Number(new URL(url).searchParams.get('imageId'));
    return reply({items:[{id:image,type:mode==='video'?'video':'image',username:`Artist${image}`,
      browsingLevel:1,url:'https://image.civitai.com/isolated-art.jpeg'}]});
  }
  image=JSON.parse(new URL(url).searchParams.get('input')).json.id;
  return reply({result:{data:{json:{id:image,user:{id:mode==='own'?seed:image+100000,username:`Artist${image}`},
    availability:'Public',ingestion:'Scanned',needsReview:null,publishedAt:'2026-01-01T00:00:00Z',nsfwLevel:1}}}});
};
await import('../supabase/functions/community-v1/index.ts');
async function request(operation,fields={},token='isolated-member-0') {
  const response=await handler(new Request('https://isolated.invalid/functions/v1/community-v1',{
    method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},
    body:JSON.stringify({apiVersion:1,operation,...fields})}));
  return {status:response.status,body:await response.json()};
}
for(const token of ['expired','revoked','malformed']) {
  assert.equal((await request('status',{},token)).body.code,'AUTH_EXPIRED');
}
for(const [fault,code] of [['own','SELF_RECOMMENDATION'],['unavailable','UNAVAILABLE'],['video','INVALID_ARTWORK']]) {
  mode=fault;assert.equal((await request('recommend',{artwork:seed+100,idempotencyKey:`isolated-failed-${seed}`})).body.code,code);
  assert.equal((await request('status')).body.quota.used,0);
}
mode='';
assert.equal((await request('recommend',{artwork:seed+100,memberId:999})).body.code,'INVALID_REQUEST');
for(let index=0;index<10;index++) {
  const token=`isolated-member-${index}`;
  const status=await request('status',{},token);assert.equal(status.body.member.id,seed+index);
  const result=await request('recommend',{artwork:seed+100+index,idempotencyKey:`isolated-edge-${seed}-${index}`},token);
  assert.equal(result.status,200);assert.equal(result.body.quota.used,1);
}
const replay={artwork:seed+120,idempotencyKey:`isolated-edge-replay-${seed}`};
const replayResults=await Promise.all(Array.from({length:6},()=>request('recommend',replay)));
assert.ok(replayResults.every(result=>result.status===200));
assert.equal(replayResults.filter(result=>result.body.replayed).length,5);
assert.equal((await request('status')).body.quota.used,2);
assert.equal((await request('recommend',{artwork:seed+121,idempotencyKey:replay.idempotencyKey})).body.code,'INVALID_REQUEST');
assert.equal((await request('recommend',{artwork:seed+120,idempotencyKey:`isolated-other-${seed}`})).body.code,'DUPLICATE');
const oneSlot=await Promise.all([122,123].map(n=>request('recommend',{
  artwork:seed+n,idempotencyKey:`isolated-one-slot-${seed}-${n}`})));
assert.equal(oneSlot.filter(result=>result.status===200).length,1);
assert.equal(oneSlot.filter(result=>result.body.code==='MEMBER_QUOTA').length,1);
assert.equal((await request('status')).body.quota.used,3);
console.log(JSON.stringify({passed:true,members:10,acceptedRecommendations:12,expectedAcceptedAudits:12,
  realPostgreSQLRPC:true,concurrentReplayRequests:6,oneRemainingSlotContenders:2,rpcCalls,
  syntheticUserInfoAndMetadata:true,postgrestAndDenoTested:false}));
