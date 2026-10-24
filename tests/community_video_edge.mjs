// Production verifier; synthetic upstream and rollback-free preview RPC only.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
let fault='',previews=0,commits=0;
globalThis.Deno={env:{get:n=>({SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2'}[n])}};
const transport=async(url,options)=>{
 if(url.endsWith('/userinfo'))return Response.json({sub:'7',id:7,username:'Viewer'});
 if(url==='https://civitai.com/api/v1/me')return Response.json({id:7,tokenScope:33,subject:{type:'oauth'}});
 if(url.includes('/rpc/')){
  const name=url.split('/').at(-1),args=JSON.parse(options.body);
  if(name==='community_take_rate')return new Response(null,{status:204});
  if(name==='community_access')return Response.json({invited:true});
  if(name==='community_submit'){
   if(args.p_preview===true)previews++;else commits++;
   return Response.json({imageId:123,quota:{used:0,remaining:3}});
  }
  throw Error('Unexpected RPC '+name);
 }
 if(url.includes('/api/v1/images?'))return Response.json({items:[{id:123,type:fault==='unsupported'?'audio':'video',username:'Artist',browsingLevel:1,url:fault==='url'?'https://untrusted.invalid/video.mp4':'https://image.civitai.com/key/id/original=true/video.mp4'}]});
 if(url.includes('/api/trpc/image.get?'))return Response.json({result:{data:{json:{id:123,type:fault==='type'?'image':'video',user:fault==='owner'?{}:{id:42,username:'Artist'},availability:fault==='private'?'Unsearchable':'Public',ingestion:fault==='scan'?'Pending':'Scanned',needsReview:null,publishedAt:'2026-01-01T00:00:00Z',nsfwLevel:fault==='rating'?16:1}}}});
 throw Error('Unexpected network');
};
const handler=createCommunityHandler({fetch:transport,invitationOnly:true,galleryWindow:true});
for(const [mode,expected] of [['',null],['unsupported','INVALID_ARTWORK'],['type','INVALID_ARTWORK'],['owner','UNVERIFIED_OWNER'],['private','INVALID_ARTWORK'],['scan','INVALID_ARTWORK'],['rating','INVALID_ARTWORK'],['url','INVALID_ARTWORK']]){
 fault=mode;
 const response=await handler(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer synthetic-read-only','Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation:'verify',artwork:123})}));
 const body=await response.json();assert.equal(body.code??null,expected,mode);assert.equal(response.status,expected?400:200,mode);
}
assert.equal(previews,1);assert.equal(commits,0);
console.log(JSON.stringify({passed:true,videoEligibilityScenarios:8,previewOnly:true,recommendationCommits:commits,hostedRequests:0}));
