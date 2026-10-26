import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
const config={SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2'};
globalThis.Deno={env:{get:k=>config[k]}};
let invited=true,provider=0,limit,submitted=0;
const h=createCommunityHandler({invitationOnly:true,recommendationList:true,publicArtwork:true,authenticate:async()=>({id:7,username:'Verified'}),fetch:async(url,o)=>{
 if(!url.includes('/rpc/')){provider++;assert.equal(o.headers.Authorization,undefined);if(url.includes('/api/v1/images?'))return Response.json({items:[{id:101,postId:45,type:'video',username:'Artist',browsingLevel:1,url:'https://image.civitai.com/a/b/width=512/101.mp4'}]});if(url.endsWith('/posts/45'))return Response.json({id:45,publishedAt:'2026-01-01T00:00:00Z',user:{id:99,username:'Artist'},images:[]});throw Error('Unsupported provider');}
 const n=url.split('/').at(-1),a=JSON.parse(o.body);
 if(n==='community_take_rate')return new Response(null,{status:204});
 if(n==='community_access')return invited?Response.json({accessMode:'invite_only'}):Response.json({message:'INVITE_ONLY'},{status:400});
 if(n==='community_recommendation_list'){limit=a.p_limit;assert.equal('p_filters' in a,false);return Response.json({apiVersion:1,feedVersion:3,items:[],nextCursor:null});}
 if(n==='community_submit'){assert.equal(a.p_creator,99);submitted++;return Response.json({apiVersion:1,preview:true});}
 throw Error('Obsolete/unexpected RPC '+n);
}});
async function call(extra={},operation='gallery'){return h(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer synthetic','Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation,...extra})}));}
let passed=0;
for(const [args,n] of [[{},24],[{limit:50},50],[{limit:24,cursor:123},24]]){assert.equal((await call(args)).status,200);assert.equal(limit,n);passed++;}
for(const args of [{limit:0},{limit:51},{filters:{tags:['private']}},{cursor:-1}]){assert.equal((await call(args)).status,400);passed++;}
assert.equal(provider,0);invited=false;assert.equal((await call()).status,403);passed++;
invited=true;config.COMMUNITY_BETA_STATE='disabled';assert.equal((await call()).status,503);passed++;
config.COMMUNITY_BETA_STATE='enabled';assert.equal((await call({artwork:101},'verify')).status,200);assert.equal(submitted,1);assert.equal(provider,2);passed++;
console.log(JSON.stringify({passed,failed:0,zeroGalleryArtworkCalls:true,submissionAnonymousOwnershipVerified:true,hostedRequests:0}));
