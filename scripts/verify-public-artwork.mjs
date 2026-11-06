// Read-only provider acceptance through the candidate handler. SQL/auth are
// simulated locally: this is NOT a hosted login or recommendation acceptance.
import assert from 'node:assert/strict';
import {createCommunityHandler} from '../supabase/functions/community-v1/handler.ts';
globalThis.Deno={env:{get:key=>({SUPABASE_URL:'https://isolated.invalid',SUPABASE_SERVICE_ROLE_KEY:'synthetic',COMMUNITY_BETA_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2,4,8,16'}[key])}};
let reads=0,previews=0;
const transport=async(url,options)=>{
 if(url.startsWith('https://isolated.invalid/rest/v1/rpc/')) {
  const name=url.split('/').at(-1);
  if(name==='community_take_rate')return new Response(null,{status:204});
  if(name==='community_access')return Response.json({invited:true});
  assert.equal(name,'community_submit');
  const args=JSON.parse(options.body);assert.equal(args.p_preview,true);
  assert(Number.isSafeInteger(args.p_creator)&&args.p_creator>0);previews++;
  return Response.json({apiVersion:1,preview:true});
 }
 assert(['https://civitai.red/api/v1/images?imageId=146530669&limit=1&browsingLevel=31'].includes(url)||/^https:\/\/civitai\.red\/api\/v1\/posts\/[1-9][0-9]*$/.test(url));
 assert.equal(options.headers.Authorization,undefined);
 assert.equal(options.redirect,'manual');reads++;
 return fetch(url,options);
};
try {
 const handler=createCommunityHandler({fetch:transport,invitationOnly:true,publicArtwork:true,authenticate:async()=>({id:11107642,username:'Local test identity; not authenticated'})});
 const response=await handler(new Request('https://isolated.invalid',{method:'POST',headers:{Authorization:'Bearer sdc_'+'x'.repeat(43),'Content-Type':'application/json'},body:JSON.stringify({apiVersion:1,operation:'verify',artwork:146530669})}));
 const result=await response.json();
 assert.equal(response.status,200);assert.equal(result.preview,true);assert.equal(reads,2);assert.equal(previews,1);
 console.log(JSON.stringify({passed:true,liveAnonymousGETs:reads,knownPublicVideo:146530669,ownerKeyUsed:false,userTokensUsed:false,identitySimulated:true,previewSQLSimulated:true,hostedRequests:0,recommendationWrites:0}));
}catch {
 console.log(JSON.stringify({passed:false,liveAnonymousGETs:reads,hostedRequests:0,recommendationWrites:0,providerBodyLogged:false}));process.exitCode=1;
}
