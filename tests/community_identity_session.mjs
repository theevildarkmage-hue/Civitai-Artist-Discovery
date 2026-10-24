// Full candidate Edge entry factory, real synthetic ES256 signatures. SQL and
// provider transports are simulated here; native SQL tests are separate.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createIdentityCommunityHandler} from '../supabase/functions/community-v1/identity-handler.ts';
const env={SUPABASE_URL:'https://isolated.supabase.co',SUPABASE_SERVICE_ROLE_KEY:'synthetic-service',
  DISCOVERY_IDENTITY_CHALLENGE_KEY:Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('base64url'),
  COMMUNITY_BETA_STATE:'enabled',DISCOVERY_REGISTRY_STATE:'enabled',COMMUNITY_ALLOWED_RATINGS:'1,2'};
globalThis.Deno={env:{get:name=>env[name]}};
const native=process.env.CAD_TEST_IDENTITY_NATIVE==='1';
if(native && (process.env.CAD_ALLOW_ISOLATED_DB_TESTS!=='1'||process.env.PGHOST!=='127.0.0.1'||!process.env.CAD_TEST_PG_SYSTEM_IDENTIFIER))throw Error('Owned native cluster required');
let clock=native?Date.now():1800000000000,sql=0,registers=0,providerCalls=0,invited=true,metadataAssetStatus=200;
const sessionHashes=new Map(),replays=new Set(),wire=[];
const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
const jwk={...await crypto.subtle.exportKey('jwk',pair.publicKey),kid:'synthetic',alg:'ES256',use:'sig'};
const b64=x=>Buffer.from(x).toString('base64url');
async function nativeSQL(statement,service=true) {
 const role=service?{PGUSER:'service_role',PGPASSWORD:JSON.parse(process.env.CAD_TEST_PG_ROLE_PASSWORDS).service_role}:{};
 return new Promise((resolve,reject)=>{
  const child=spawn('psql',['-X','-A','-t','-v','ON_ERROR_STOP=1'],{env:{...process.env,...role},windowsHide:true});
  let stdout='',stderr='';child.stdout.on('data',data=>stdout+=data);child.stderr.on('data',data=>stderr+=data);
  const timer=setTimeout(()=>{child.kill();reject(Error('Native SQL timeout'));},30000);
  child.on('error',()=>{clearTimeout(timer);reject(Error('Native SQL unavailable'));});
  child.on('close',code=>{clearTimeout(timer);resolve({code,stdout:stdout.trim(),authExpired:stderr.includes('AUTH_EXPIRED')});});child.stdin.end(statement);
 });
}
if(native) {
 const identity=await nativeSQL('select system_identifier from pg_control_system();',false);
 assert.equal(identity.stdout,process.env.CAD_TEST_PG_SYSTEM_IDENTIFIER);
 const directory=await nativeSQL('show data_directory;',false);
 assert.equal(directory.stdout.replaceAll('\\','/').toLowerCase(),process.env.CAD_TEST_PG_DATA_DIR.replaceAll('\\','/').toLowerCase());
}
async function assertion(challenge,patch={}) {
 const claims={iss:'https://auth.civitai.com',aud:challenge.audience,sub:'7',nonce:challenge.nonce,iat:Math.floor(clock/1000),exp:Math.floor(clock/1000)+3600,...patch};
 const input=b64(JSON.stringify({alg:'ES256',typ:'JWT',kid:'synthetic'}))+'.'+b64(JSON.stringify(claims));
 return input+'.'+b64(await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},pair.privateKey,new TextEncoder().encode(input)));
}
async function transport(url,options={}) {
 wire.push({url,options});
 if(url.includes('/rpc/')) {
  sql++;const name=url.split('/').at(-1),body=JSON.parse(options.body);
  if(native&&['community_identity_exchange','community_identity_resolve','community_identity_revoke'].includes(name)) {
   const args=Object.entries(body).map(([key,value])=>{
    assert.match(key,/^p_[a-z_]+$/);assert(['string','number'].includes(typeof value));
    return key+'=>'+(typeof value==='number'?String(value):"'"+value.replaceAll("'","''")+"'");
   }).join(',');
   const result=await nativeSQL('select public.'+name+'('+args+');');
   return result.code===0?Response.json(JSON.parse(result.stdout)):Response.json({message:result.authExpired?'AUTH_EXPIRED':'UNAVAILABLE'},{status:400});
  }
  if(name==='community_identity_exchange') {
   for(const field of ['p_assertion_hash','p_nonce_hash','p_session_hash'])assert.match(body[field],/^[0-9a-f]{64}$/);
   if(replays.has(body.p_assertion_hash)||replays.has(body.p_nonce_hash))return Response.json({message:'AUTH_EXPIRED'},{status:400});
   replays.add(body.p_assertion_hash);replays.add(body.p_nonce_hash);
   const expiresAt=Math.floor(clock/1000)+43200;sessionHashes.set(body.p_session_hash,{id:body.p_member,username:body.p_username,expiresAt});
   return Response.json({expiresAt});
  }
  if(name==='community_identity_resolve') {
   const identity=sessionHashes.get(body.p_session_hash);
   return identity&&identity.expiresAt>clock/1000?Response.json(identity):Response.json({message:'AUTH_EXPIRED'},{status:400});
  }
  if(name==='community_identity_revoke'){sessionHashes.delete(body.p_session_hash);return Response.json({revoked:true});}
  if(name==='community_take_rate')return new Response(null,{status:204});
  if(name==='community_access')return invited?Response.json({invited:true}):Response.json({message:'INVITE_ONLY'},{status:400});
  if(name==='community_registry_upsert'){registers++;return Response.json({apiVersion:1,registered:true});}
  if(name==='community_quota')return Response.json({apiVersion:1,remaining:3,used:0,limit:3});
  if(name==='community_submit'){assert.equal(body.p_preview,true);assert.equal(body.p_creator,9);return Response.json({apiVersion:1,preview:true});}
  throw Error('Unexpected RPC '+name);
 }
 providerCalls++;
 if(url.startsWith('https://civitai.red/api/v1/images?')) {
  assert.equal(options.headers.Authorization,undefined);
  if(metadataAssetStatus!==200)return Response.json({},{status:metadataAssetStatus});
  return Response.json({items:[{id:123,postId:45,type:'video',username:'Artist',browsingLevel:1,url:'https://image.civitai.com/synthetic.mp4'}]});
 }
 if(url==='https://civitai.red/api/v1/posts/45') {
  assert.equal(options.headers.Authorization,undefined);
  return Response.json({id:45,user:{id:9,username:'Artist'},publishedAt:'2026-01-01T00:00:00Z',images:[{id:123,postId:45,type:'video',browsingLevel:1}]});
 }
 assert(!options.headers?.Authorization,'No tester bearer may reach provider in identity flow');
 if(url==='https://auth.civitai.com/.well-known/jwks.json')return Response.json({keys:[jwk]});
 if(url==='https://civitai.red/api/v1/users?ids=7')return Response.json({items:[{id:7,username:'Verified'}]});
 throw Error('Unexpected provider request');
}
let passed=0;
async function check(name,fn){try {await fn();passed++;}catch(error){throw new Error(name,{cause:error});}}
const create=()=>createIdentityCommunityHandler({fetch:transport,env:name=>env[name],clock:()=>clock});
const first=create(),second=create();
async function call(handler,body,token,headers={}) {
 const response=await handler(new Request('https://isolated.supabase.co/functions/v1/community-v1',{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...headers},body:JSON.stringify(body)}));
 return {status:response.status,...await response.json()};
}
const challenge=await call(first,{apiVersion:2,operation:'identityChallenge'});
await check('challenge has no SQL or provider side effects',()=>{assert.equal(challenge.apiVersion,2);assert.equal(sql,0);assert.equal(providerCalls,0);});
const proof=await assertion(challenge);
let exchanged;
await check('cross-instance exchange uses server-bound nonce',async()=>{
 exchanged=await call(second,{apiVersion:2,operation:'identityExchange',challenge:challenge.challenge,identityAssertion:proof});
 assert.equal(exchanged.status,200);assert.match(exchanged.session,/^sdc_[A-Za-z0-9_-]{43}$/);assert.equal(exchanged.member.id,7);
});
await check('raw assertion and session absent from SQL arguments',()=>{
 for(const request of wire.filter(x=>x.url.includes('/rpc/'))){assert(!request.options.body.includes(proof));assert(!request.options.body.includes(exchanged.session));}
});
await check('replay rejected on another instance',async()=>assert.equal((await call(first,{apiVersion:2,operation:'identityExchange',challenge:challenge.challenge,identityAssertion:proof})).status,401));
await check('registry uses session and remains independent of invitations',async()=>{
 invited=false;assert.equal((await call(first,{apiVersion:1,operation:'register',appVersion:'3.0.0-beta.test',platform:'Windows'},exchanged.session)).registered,true);assert.equal(registers,1);
 assert.equal((await call(first,{apiVersion:1,operation:'status'},exchanged.session)).code,'INVITE_ONLY');invited=true;
});
await check('normal Community status retains quota enforcement',async()=>assert.equal((await call(first,{apiVersion:1,operation:'status'},exchanged.session)).status,200));
await check('video preview uses only anonymous artwork metadata',async()=>{
 assert.equal((await call(first,{apiVersion:1,operation:'verify',artwork:123},exchanged.session)).preview,true);
 for(const request of wire.filter(x=>x.url.startsWith('https://civitai')))assert.notEqual(request.options.headers?.Authorization,'Bearer '+exchanged.session);
});
await check('anonymous metadata auth failure is service failure, not user sign-out',async()=>{
 metadataAssetStatus=401;
 const result=await call(first,{apiVersion:1,operation:'verify',artwork:123},exchanged.session);
 assert.equal(result.status,503);assert.equal(result.code,'UNAVAILABLE');metadataAssetStatus=200;
});
await check('disabled registry creates no registry row',async()=>{
 env.DISCOVERY_REGISTRY_STATE='off';const before=registers;
 assert.equal((await call(first,{apiVersion:1,operation:'register',appVersion:'test',platform:'Windows'},exchanged.session)).registered,false);
 assert.equal(registers,before);env.DISCOVERY_REGISTRY_STATE='enabled';
});
await check('disabled Community stays disabled after session authentication',async()=>{
 env.COMMUNITY_BETA_STATE='disabled';assert.equal((await call(first,{apiVersion:1,operation:'status'},exchanged.session)).beta.submissions,false);assert.equal((await call(first,{apiVersion:1,operation:'gallery'},exchanged.session)).code,'BETA_DISABLED');env.COMMUNITY_BETA_STATE='enabled';
});
await check('both services off avoids identity exchange and session writes',async()=>{
 const before=[sql,providerCalls];env.COMMUNITY_BETA_STATE='disabled';env.DISCOVERY_REGISTRY_STATE='off';
 assert.equal((await call(first,{apiVersion:2,operation:'identityChallenge'})).available,false);
 assert.equal((await call(first,{apiVersion:2,operation:'identityExchange',challenge:challenge.challenge,identityAssertion:proof})).available,false);
 assert.deepEqual([sql,providerCalls],before);env.COMMUNITY_BETA_STATE='enabled';env.DISCOVERY_REGISTRY_STATE='enabled';
});
await check('legacy and opaque tokens cause zero provider or SQL requests',async()=>{
 const before=[sql,providerCalls];
 for(const token of ['civitai_synthetic_write','civitai_synthetic_read',proof])
  assert.equal((await call(first,{apiVersion:1,operation:'status'},token)).status,401);
 assert.deepEqual([sql,providerCalls],before);
});
await check('exchange refuses bearer headers',async()=>assert.equal((await call(first,{apiVersion:2,operation:'identityExchange'},'civitai_synthetic')).status,401));
await check('browser origin rejected',async()=>assert.equal((await call(first,{apiVersion:2,operation:'identityChallenge'},null,{Origin:'https://attacker.invalid'})).status,403));
await check('tampered challenge rejected without provider work',async()=>{
 const before=providerCalls;
 assert.equal((await call(first,{apiVersion:2,operation:'identityExchange',challenge:challenge.challenge+'x',identityAssertion:proof})).status,401);assert.equal(providerCalls,before);
});
await check('wrong nonce rejected before session SQL',async()=>{
 const other=await call(first,{apiVersion:2,operation:'identityChallenge'}),before=sql;
 assert.equal((await call(first,{apiVersion:2,operation:'identityExchange',challenge:other.challenge,identityAssertion:proof})).status,401);assert.equal(sql,before);
});
await check('concurrent exchanges admit exactly one session',async()=>{
 const next=await call(first,{apiVersion:2,operation:'identityChallenge'}),token=await assertion(next);
 const responses=await Promise.all([first,second].map(handler=>call(handler,{apiVersion:2,operation:'identityExchange',challenge:next.challenge,identityAssertion:token})));
 assert.deepEqual(responses.map(x=>x.status).sort(),[200,401]);
});
await check('expired challenge rejected',async()=>{
 clock+=301000;const before=providerCalls;
 assert.equal((await call(first,{apiVersion:2,operation:'identityExchange',challenge:challenge.challenge,identityAssertion:proof})).status,401);assert.equal(providerCalls,before);
 if(native)clock-=301000;
});
await check('sign-out revokes only its Discovery session',async()=>{
 const next=await call(first,{apiVersion:2,operation:'identityChallenge'}),token=await assertion(next);
 const other=await call(first,{apiVersion:2,operation:'identityExchange',challenge:next.challenge,identityAssertion:token});
 assert.equal((await call(second,{apiVersion:1,operation:'identityLogout'},other.session)).revoked,true);
 assert.equal((await call(first,{apiVersion:1,operation:'status'},other.session)).status,401);
 assert.equal((await call(first,{apiVersion:1,operation:'status'},exchanged.session)).status,200);
});
await check('expired session denied',async()=>{
 if(native) {
  const digest=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(exchanged.session))).toString('hex');
  const result=await nativeSQL("update public.community_identity_sessions set created_at=clock_timestamp()-interval '2 hours',expires_at=clock_timestamp()-interval '1 hour' where session_hash='"+digest+"';",false);assert.equal(result.code,0);
 }
 clock+=43200000;assert.equal((await call(first,{apiVersion:1,operation:'status'},exchanged.session)).status,401);
});
console.log(JSON.stringify({passed,failed:0,realSyntheticSignatures:true,providerSimulated:true,identitySQLNative:native,otherRPCsSimulated:true,hostedRequests:0}));
