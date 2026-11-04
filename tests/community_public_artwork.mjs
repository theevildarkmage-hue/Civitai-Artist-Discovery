// Candidate business handler; simulated public API and SQL. No hosted writes.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
const config={SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2'};
globalThis.Deno={env:{get:key=>config[key]}};
let mode='good',submitCalls=0,providerCalls=0,invited=true,galleryCalls=0;
const transport=async(url,options)=>{
 if(url.includes('/rpc/')) {
  const name=url.split('/').at(-1),args=JSON.parse(options.body);
  if(name==='community_take_rate')return new Response(null,{status:204});
  if(name==='community_access')return invited?Response.json({invited:true}):Response.json({message:'INVITE_ONLY'},{status:400});
  if(name==='community_recommendation_list'){
   galleryCalls++;assert.equal(args.p_member,7);assert.equal(args.p_limit,24);
   return Response.json({apiVersion:1,feedVersion:3,items:[],nextCursor:null});
  }
  if(name==='community_submit'){
   submitCalls++;assert.equal(args.p_preview,true);assert.equal(args.p_creator,9);
   return Response.json({apiVersion:1,preview:true});
  }
  throw Error('Unexpected SQL');
 }
 providerCalls++;assert.equal(options.headers.Authorization,undefined,'No credential may reach artwork APIs');
 assert.equal(options.redirect,'manual');
 assert(options.signal instanceof AbortSignal);
 if(url.startsWith('https://civitai.red/api/v1/images?')) {
  if(['deleted','forbidden','unauthorized','overload','limited','redirect'].includes(mode))return Response.json({},{status:{deleted:404,forbidden:403,unauthorized:401,overload:503,limited:429,redirect:302}[mode]});
  if(mode==='empty')return Response.json({items:[]});
  const item={id:123,postId:45,type:mode==='video'?'video':'image',username:'Artist',browsingLevel:mode==='rating'?16:1,url:'https://image.civitai.com/synthetic'};
  if(mode==='wrongId')item.id=124;
  if(mode==='badPostId')item.postId='45';
  if(mode==='badMedia')item.url='https://attacker.invalid/image';
  if(mode==='badType')item.type='audio';
  if(mode==='badRating')item.browsingLevel=3;
  return Response.json({items:mode==='duplicate'?[item,item]:[item]});
 }
 if(url==='https://civitai.red/api/v1/posts/45') {
  if(mode==='postDeleted')return Response.json({},{status:404});
  const post={id:45,user:{id:9,username:'Artist'},publishedAt:'2026-01-01T00:00:00Z',images:[{id:123,postId:45,type:mode==='video'?'video':'image',browsingLevel:1}]};
  if(mode==='rating')post.images[0].browsingLevel=16;
  if(mode==='unlisted')post.availability='Unsearchable';
  if(mode==='largePost')post.images=[]; // First-100 preview omission is not deletion.
  if(mode==='wrongPost')post.id=46;
  if(mode==='wrongName')post.user.username='Other';
  if(mode==='badOwner')post.user.id=0;
  if(mode==='future')post.publishedAt='2099-01-01T00:00:00Z';
  if(mode==='badDate')post.publishedAt='invalid';
  if(mode==='ratingRace')post.images[0].browsingLevel=2;
  return Response.json(post);
 }
 throw Error('Unexpected provider route');
};
const handler=createCommunityHandler({fetch:transport,invitationOnly:true,recommendationList:true,publicArtwork:true,authenticate:async()=>({id:7,username:'Verified'})});
async function call(operation='verify') {
 return handler(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer sdc_'+ 'x'.repeat(43),'Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation,...(operation==='verify'?{artwork:123}:{})})}));
}
let passed=0;
for(const scenario of ['good','video','unlisted','largePost']) {
 mode=scenario;const response=await call();assert.equal(response.status,200,scenario);assert.equal((await response.json()).preview,true);passed++;
}
for(const [scenario,code] of [['deleted','INVALID_ARTWORK'],['forbidden','INVALID_ARTWORK'],['empty','INVALID_ARTWORK'],['postDeleted','INVALID_ARTWORK'],['unauthorized','UNAVAILABLE'],['overload','UNAVAILABLE'],['limited','RATE_LIMITED'],['redirect','UNAVAILABLE'],['wrongId','INVALID_ARTWORK'],['badPostId','INVALID_ARTWORK'],['badMedia','INVALID_ARTWORK'],['badType','INVALID_ARTWORK'],['badRating','INVALID_ARTWORK'],['duplicate','INVALID_ARTWORK'],['wrongPost','INVALID_ARTWORK'],['wrongName','UNVERIFIED_OWNER'],['badOwner','INVALID_ARTWORK'],['future','INVALID_ARTWORK'],['badDate','INVALID_ARTWORK'],['ratingRace','INVALID_ARTWORK'],['rating','CONTENT_FILTERED']]) {
 mode=scenario;const before=submitCalls,response=await call();assert.equal((await response.json()).code,code,scenario);assert.equal(submitCalls,before,scenario);passed++;
}
// Accepted gallery reads never consult the current provider or review leases.
// Submission verification above retains all eligibility/rating negative cases.
for(const scenario of ['good','video','unlisted','largePost','deleted','empty','postDeleted','rating','overload','unauthorized','limited']) {
 mode=scenario;const upstreamBefore=providerCalls,listBefore=galleryCalls;
 assert.equal((await call('gallery')).status,200,scenario);
 assert.equal(providerCalls,upstreamBefore,scenario);assert.equal(galleryCalls,listBefore+1);passed++;
}
mode='good';invited=false;let before=providerCalls;
assert.equal((await (await call()).json()).code,'INVITE_ONLY');assert.equal(providerCalls,before);passed++;
invited=true;config.COMMUNITY_BETA_STATE='read_only';before=providerCalls;
assert.equal((await (await call()).json()).code,'READ_ONLY');assert.equal(providerCalls,before);passed++;
config.COMMUNITY_BETA_STATE='disabled';
assert.equal((await (await call('gallery')).json()).code,'BETA_DISABLED');assert.equal(providerCalls,before);passed++;
console.log(JSON.stringify({passed,failed:0,providerSimulated:true,sqlSimulated:true,hostedRequests:0,recommendationWrites:0,anonymousArtworkOnly:true}));
