// Test launcher only. The production factory retains fixed Civitai hosts by default.
// Transport redirection is confined here, outside the deployed function directory.
import {createCommunityHandler} from '../functions/community-v1/handler.ts';
const issuer=Deno.env.get('CAD_TEST_UPSTREAM_URL')!;
const database=Deno.env.get('SUPABASE_URL')!;
const port=Number(Deno.env.get('CAD_TEST_EDGE_PORT'));
const live=Deno.env.get('CAD_TEST_LIVE_READONLY')==='1';
for(const value of [issuer,database]) {
  const url=new URL(value);
  if(url.protocol!=='http:' || url.hostname!=='127.0.0.1') throw Error('Loopback test targets required');
}
const transport:typeof fetch=(input,options)=>{
  const url=String(input);
  if(url==='https://auth.civitai.com/api/auth/oauth/userinfo') return fetch(live?url:issuer+'/userinfo',options);
  if(url.startsWith('https://civitai.red/api/')) return fetch(live?url:issuer+new URL(url).pathname+new URL(url).search,options);
  if(url.startsWith(database+'/rest/v1/rpc/')) return fetch(url.replace('/rest/v1/','/'),options);
  throw Error('Unapproved test transport destination');
};
// A shortened ingress window keeps bounded stress tests fast. SQL member/token
// counters and the production concurrency/body/timeout defaults are unchanged.
const handler=createCommunityHandler({fetch:transport,ipLimit:200,ipWindowMs:1000,
  invitationOnly:Deno.env.get('CAD_TEST_PRIVATE_BETA')==='1',distributedLimit:Deno.env.get('CAD_TEST_DISTRIBUTED_LIMIT')==='1'});
Deno.serve({hostname:'127.0.0.1',port,onListen:()=>{}},async(request,info)=>{
  if(live) {
    const body=await request.clone().json().catch(()=>null);
    if(!body || !['status','verify'].includes(body.operation)) return new Response('{}',{status:403});
  }
  const response=await handler(request,info);
  // Owned local fault injection: SQL commits, then the client gets no success
  // representation. Never deployed; proves explicit same-key recovery over HTTP.
  if(request.headers.get('X-CAD-Test-Lost-Reply')==='1' && response.ok)
    return Response.json({code:'UNAVAILABLE'},{status:503});
  return response;
});
