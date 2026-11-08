// Submission ratings are independent of the reader's display preferences.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
const config={SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled'};
globalThis.Deno={env:{get:k=>config[k]}};
let level=1,kind='image',writes=0,preview=0;
const transport=async(url,options)=>{
 if(url.includes('/rpc/')) {
  const name=url.split('/').at(-1),body=JSON.parse(options.body);
  if(name==='community_take_rate')return new Response(null,{status:204});
  if(name==='community_access')return Response.json({invited:true});
  if(name==='community_submit'){
   body.p_preview?preview++:writes++;
   return Response.json({preview:body.p_preview,imageId:123,quota:{used:0,remaining:3}});
  }
  throw Error('Unexpected SQL');
 }
 assert.equal(options.headers.Authorization,undefined,'Never send Civitai credentials for anonymous eligibility');
 if(url.includes('/api/v1/images?'))return Response.json({items:[{id:123,postId:45,type:kind,username:'Artist',browsingLevel:level,url:'https://image.civitai.com/public'}]});
 if(url.endsWith('/api/v1/posts/45'))return Response.json({id:45,user:{id:9,username:'Artist'},publishedAt:'2026-01-01T00:00:00Z',images:[{id:123,postId:45,type:kind,browsingLevel:level}]});
 throw Error('Unexpected provider');
};
const handler=createCommunityHandler({fetch:transport,publicArtwork:true,invitationOnly:true,authenticate:async()=>({id:7,username:'Member'})});
async function call(operation='verify') {
 return handler(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer sdc_'+ 'x'.repeat(43),'Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation,artwork:123})}));
}
for(kind of ['image','video'])for(level of [1,2,4,8,16])for(const operation of ['verify','recommend']) {
 const response=await call(operation);assert.equal(response.status,200,`${kind}/${level}/${operation}`);
}
assert.equal(writes,10);assert.equal(preview,10);
config.COMMUNITY_ALLOWED_RATINGS='1,2';level=4;
assert.equal((await (await call()).json()).code,'CONTENT_FILTERED','Explicit restricted operating policy still applies');
config.COMMUNITY_ALLOWED_RATINGS='invalid';assert.equal((await call()).status,503);
config.COMMUNITY_ALLOWED_RATINGS='1,2,4,8,16';level=3;assert.equal((await call()).status,400);
assert.equal(writes,10);
console.log('PASS: all five valid ratings x image/video x verify/submit; explicit restrictions, malformed ratings and anonymous-only checks preserved; synthetic provider/SQL');
