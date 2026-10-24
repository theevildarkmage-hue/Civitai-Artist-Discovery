import {identityTransport} from './community_identity_transport.mjs';
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
let state, ratings, commits=0;
globalThis.Deno={env:{get:name=>({COMMUNITY_BETA_STATE:state,COMMUNITY_ALLOWED_RATINGS:ratings,SUPABASE_URL:'https://owned.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic'}[name])}};
const transport=async url=>{
  const value=x=>Response.json(x);
  if(url.includes('community_take_rate'))return new Response(null,{status:204});
  if(url.includes('community_quota'))return value({used:0,remaining:3});
  if(url.includes('community_gallery'))return value({items:[],nextCursor:null});
  if(url.includes('community_submit')){commits++;return value({imageId:123});}
  if(url.includes('/userinfo'))return value({sub:'7',id:7,username:'Member'});
  if(url.includes('/api/v1/images'))return value({items:[{id:123,type:'image',username:'Artist',browsingLevel:8,url:'https://image.civitai.com/public.jpeg'}]});
  if(url.includes('/image.get'))return value({result:{data:{json:{id:123,user:{id:42,username:'Artist'},availability:'Public',ingestion:'Scanned',publishedAt:'2026-01-01T00:00:00Z',nsfwLevel:8}}}});
  throw Error('Unexpected transport');
};
const handler=createCommunityHandler({fetch:identityTransport(transport)});
async function call(operation,extra={}){
  const r=await handler(new Request('https://owned.invalid',{method:'POST',headers:{Authorization:'Bearer local-test','Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation,...extra})}));
  return [r.status,await r.json()];
}
assert.equal((await call('gallery'))[0],503);
for(const invalid of ['unknown','true','ENABLED']){state=invalid;assert.equal((await call('recommend',{artwork:123}))[1].code,'BETA_DISABLED');}
state='read_only';assert.equal((await call('gallery'))[0],200);assert.equal((await call('recommend',{artwork:123}))[1].code,'READ_ONLY');
assert.equal((await call('status'))[1].beta.submissions,false);assert.equal(commits,0);
state='enabled';assert.equal((await call('recommend',{artwork:123}))[1].code,'CONTENT_FILTERED');assert.equal(commits,0);
ratings='8';assert.equal((await call('recommend',{artwork:123}))[0],200);assert.equal(commits,1);
state='read_only';assert.equal((await call('recommend',{artwork:123,submissions:true}))[0],400);assert.equal(commits,1);
ratings='invalid';state='enabled';assert.equal((await call('recommend',{artwork:123}))[0],503);assert.equal(commits,1);
console.log('PASS: missing/invalid kill switch fails closed; read-only browse; fresh server toggle; authoritative default ratings; forged policy denied');
