import {identityTransport} from './community_identity_transport.mjs';
// Actual candidate entry point, synthetic Civitai/SQL transport only.
import assert from 'node:assert/strict';
let handler,member=7,state='enabled',admitted=true,blocked=false,invalid=false,candidates=[],calls=[],fault='';
globalThis.Deno={env:{get:n=>({SUPABASE_URL:'https://fixture.invalid',
  SUPABASE_SERVICE_ROLE_KEY:'synthetic-only',COMMUNITY_BETA_STATE:state,COMMUNITY_ALLOWED_RATINGS:'1,2'}[n])},serve:fn=>handler=fn};
globalThis.fetch=async(url,options={})=>{
  if(url.includes('/rpc/')) {
    const name=url.split('/').at(-1),args=JSON.parse(options.body);calls.push({name,args});
    if(name==='community_take_rate')return new Response(null,{status:204});
    if(name==='community_access')return Response.json(admitted&&member===7?{invited:true}:{message:'FORBIDDEN'},{status:admitted&&member===7?200:400});
    if(name==='community_review_candidates')return Response.json(candidates);
    if(name==='community_record_review')return Response.json({recorded:true});
    if(name==='community_beta_gallery')return Response.json({apiVersion:1,items:[],nextCursor:null});
    if(name==='community_report')return Response.json({reported:true});
    if(name.startsWith('community_submit'))return Response.json(blocked?{message:'SUBMITTER_BLOCKED'}:{imageId:123},{status:blocked?400:200});
    if(name==='community_quota')return Response.json({used:0,remaining:3,limit:3});
    throw Error('Unexpected privileged RPC '+name);
  }
  if(url.endsWith('/userinfo'))return Response.json(invalid?{}:{sub:String(member),id:member,username:'SyntheticMember'},{status:invalid?401:200});
  if(fault==='transient')return Response.json({},{status:503});
  if(fault==='missing')return Response.json({},{status:404});
  if(fault==='private')return Response.json({},{status:403});
  if(url.includes('/api/v1/images'))return Response.json({items:[{id:123,type:'image',username:'Artist',browsingLevel:fault==='rating'?16:1,url:'https://image.civitai.com/fixture.jpeg'}]});
  if(url.includes('/image.get'))return Response.json({result:{data:{json:{id:123,user:{id:fault==='owner'?43:42,username:'Artist'},availability:'Public',ingestion:'Scanned',publishedAt:'2026-01-01T00:00:00Z',nsfwLevel:fault==='rating'?16:1}}}});
  throw Error('Unexpected transport');
};
globalThis.fetch=identityTransport(globalThis.fetch);
await import('../supabase/functions/community-v1/index.ts');
let checks=0;
async function check(expected,operation='status',extra={},headers={}) {
  calls=[];
  const response=await handler(new Request('https://fixture.invalid/?memberId=7&role=community_operator',{
    method:'POST',headers:{Authorization:'Bearer synthetic-only','Content-Type':'application/json',...headers},
    body:JSON.stringify({apiVersion:1,operation,...extra})}));
  const body=await response.json();assert.equal(body.code??response.status,expected);checks++;return body;
}
member=8;
for(const operation of ['status','gallery','verify','recommend','withdraw','report'])await check('FORBIDDEN',operation);
await check('FORBIDDEN','status',{}, {'X-Member-Id':'7','X-Moderator-Id':'9001'});
member=7;admitted=false;
await check('FORBIDDEN');admitted=true;
for(const extra of [{memberId:7},{moderatorId:9001},{role:'service_role'},{invitationOnly:false},{sub:7},{submissions:true}])await check('INVALID_REQUEST','status',extra);
invalid=true;await check('AUTH_EXPIRED');invalid=false;
for(const operation of ['moderate','invite','block','reports','audit'])await check('INVALID_REQUEST',operation);
for(const value of [undefined,'disabled','owner_test','invalid']) {
  state=value;await check('BETA_DISABLED','report',{artwork:123,reason:'abuse'});
  assert.ok(calls.every(c=>['community_take_rate','community_access'].includes(c.name)));
}
state='read_only';assert.equal((await check(200)).beta.reporting,true);
await check(200,'report',{artwork:123,reason:'abuse'});
for(const operation of ['verify','recommend','withdraw'])await check('READ_ONLY',operation,{artwork:123});
await check('INVALID_REQUEST','report',{artwork:123,reason:'arbitrary text'});
state='enabled';blocked=true;await check('SUBMITTER_BLOCKED','recommend',{artwork:123});blocked=false;
for(const [value,expected] of [['','eligible'],['owner','owner'],['rating','rating'],['missing','unavailable'],['private','unavailable'],['transient','transient']]) {
  fault=value;candidates=[{imageId:123,creatorId:42,lease:'00000000-0000-0000-0000-000000000001'}];
  await check(200,'gallery');
  const review=calls.find(c=>c.name==='community_record_review');assert.equal(review.args.p_failure,expected==='eligible'?null:expected);
  assert.equal(review.args.p_member,7);assert.equal(review.args.p_lease,candidates[0].lease);
}
candidates=Array(4).fill(candidates[0]);await check('UNAVAILABLE','gallery');
console.log(JSON.stringify({passed:true,checks,actualCandidateEntryPoint:true,syntheticTransport:true}));
