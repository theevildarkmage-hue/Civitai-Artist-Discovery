// Production handler with synthetic transport; native SQL/HTTP tests run separately.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
globalThis.Deno={env:{get:name=>({SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2'}[name])}};
let mode='good',verdicts=[],sqlCalls=0,publicRequests=0,detailRequests=0;
const transport=async(url,options)=>{
 const authorization=options.headers.Authorization;
 if(url.endsWith('/userinfo'))return Response.json({sub:'7',id:7,username:'Verified'});
 if(url==='https://civitai.com/api/v1/me')return Response.json({id:7,tokenScope:33,subject:{type:'oauth'}});
 if(url.includes('/rpc/')) {
  sqlCalls++;const operation=url.split('/').at(-1),args=JSON.parse(options.body);
  if(operation==='community_take_rate')return new Response(null,{status:204});
  if(operation==='community_access')return Response.json({invited:true});
  if(operation==='community_prepare_gallery') {
   assert.equal(args.p_cursor,123);assert.equal(args.p_limit,24);
   return Response.json([{imageId:101,creatorId:99,lease:'00000000-0000-0000-0000-000000000001'}]);
  }
  if(operation==='community_record_review'){verdicts.push(args.p_failure);return Response.json({recorded:true});}
  if(operation==='community_beta_gallery')return Response.json({apiVersion:1,items:[],nextCursor:'120',reviewPending:false});
  throw Error('Unexpected SQL operation '+operation);
 }
 if(url.includes('/api/v1/images?')) {
  publicRequests++;assert.equal(authorization,undefined,'shared public visibility check must be anonymous');
  if(mode==='outage')return Response.json({},{status:503});
  return Response.json({items:mode==='missing'?[]:[{id:101,type:mode==='video'?'video':'image',username:'Artist',browsingLevel:mode==='rating'?16:1,url:'https://image.civitai.com/isolated.jpg'}]});
 }
 if(url.includes('/api/trpc/image.get?')) {
  detailRequests++;assert.equal(authorization,'Bearer synthetic-read-only');
  if(mode==='viewer')return Response.json({},{status:403});
  if(mode==='viewer401')return Response.json({},{status:401});
  return Response.json({result:{data:{json:{id:101,user:{id:mode==='owner'?100:99,username:'Artist'},availability:'Public',ingestion:'Scanned',needsReview:null,publishedAt:'2026-01-01T00:00:00Z',nsfwLevel:1}}}});
 }
 throw Error('Unapproved network destination');
};
const handler=createCommunityHandler({fetch:transport,invitationOnly:true,galleryWindow:true});
for(const [scenario,expected] of [['good',null],['video',null],['viewer','viewer'],['viewer401','viewer'],['owner','viewer'],['missing','unavailable'],['rating','rating'],['outage','transient']]) {
 mode=scenario;
 const response=await handler(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer synthetic-read-only','Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation:'gallery',cursor:123,limit:24})}));
 assert.equal(response.status,200,scenario);assert.equal(verdicts.at(-1),expected,scenario);
 const result=await response.json();assert.equal(result.nextCursor,'120');
 assert.equal('candidates' in result,false);assert.equal('lease' in result,false);
}
console.log(JSON.stringify({passed:true,scenarios:8,publicRequests,detailRequests,viewerFailuresNeverGlobalVerdicts:true,independentPublicRatingProof:true,upstreamCredentialsExposed:false,hostedRequests:0}));
