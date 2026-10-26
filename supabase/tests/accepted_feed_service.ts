// Disposable native integration launcher. Not a deployable entry point.
import {createIdentityCommunityHandler} from '../functions/community-v1/identity-handler.ts';
const rest=Deno.env.get('CAD_FEED_TEST_REST')!;
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(rest)||Deno.env.get('CAD_ALLOW_ISOLATED_DB_TESTS')!=='1')throw Error('Owned test required');
const counts={artwork:0,rpcs:[] as string[]};
const handler=createIdentityCommunityHandler({fetch:async(input:any,options:any)=>{
  const url=String(input),prefix='https://fixture.supabase.co/rest/v1/';
  if(!url.startsWith(prefix)){counts.artwork++;throw Error('External provider prohibited in feed test');}
  counts.rpcs.push(url.split('/').at(-1)!);
  return fetch(rest+'/'+url.slice(prefix.length),options);
}});
Deno.serve({hostname:'127.0.0.1',port:Number(Deno.env.get('CAD_FEED_TEST_PORT'))},request=>{
  if(new URL(request.url).pathname==='/metrics')return Response.json(counts);
  return handler(request);
});
