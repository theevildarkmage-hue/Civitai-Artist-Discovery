// Candidate production verifier, real ephemeral ES256 signatures. No live APIs,
// saved OAuth sessions, hosted database or application profile are accessed.
import assert from 'node:assert/strict';
import {verifyCivitaiIdentityAssertion as verify, createCivitaiIdentityVerifier, CIVITAI_ISSUER, CIVITAI_JWKS_URL} from '../supabase/functions/community-v1/identity.ts';
const encoder=new TextEncoder(), now=1800000000;
const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
const key={...await crypto.subtle.exportKey('jwk',pair.publicKey),kid:'synthetic-provider',use:'sig',alg:'ES256'};
const options={jwks:{keys:[key]},audience:'synthetic-discovery-client',nonce:'n'.repeat(43),now};
const claims={iss:CIVITAI_ISSUER,aud:options.audience,sub:'7',nonce:options.nonce,iat:now,exp:now+3600};
const header={alg:'ES256',typ:'JWT',kid:key.kid};
const b64=value=>Buffer.from(value).toString('base64url');
async function sign(payload=claims,head=header,signer=pair.privateKey) {
  const message=b64(encoder.encode(JSON.stringify(head)))+'.'+b64(encoder.encode(JSON.stringify(payload)));
  return message+'.'+b64(await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},signer,encoder.encode(message)));
}
let passed=0;
async function check(name,fn) {try {await fn();passed++;} catch(error) {throw new Error(name,{cause:error});}}
async function rejects(name,fn) {await check(name,()=>assert.rejects(fn,{message:'INVALID_IDENTITY_ASSERTION'}));}
const assertion=await sign();
await check('verified result contains only identity/time',async()=>assert.deepEqual(await verify(assertion,options),{subject:'7',issuedAt:now,expiresAt:now+3600}));
await check('extra profile claims never escape',async()=>assert.deepEqual(await verify(await sign({...claims,email:'synthetic@invalid',username:'synthetic'}),options),{subject:'7',issuedAt:now,expiresAt:now+3600}));
for(const [name,patch] of [
  ['wrong issuer',{iss:'https://attacker.invalid'}],['issuer suffix',{iss:CIVITAI_ISSUER+'/'}],
  ['wrong audience',{aud:'other'}],['audience array',{aud:[options.audience]}],['wrong azp',{azp:'other'}],
  ['missing nonce',{nonce:undefined}],['wrong nonce',{nonce:'x'.repeat(43)}],
  ['expired',{iat:now-3600,exp:now-1}],['exact expiry',{iat:now-3600,exp:now}],
  ['future issuance',{iat:now+31,exp:now+3600}],['excessive lifetime',{exp:now+3601}],
  ['missing expiry',{exp:undefined}],['fractional timestamp',{iat:now+.5}],['negative issuance',{iat:-1}],
  ['future nbf',{nbf:now+31}],['invalid auth time',{auth_time:-1}],
  ['zero subject',{sub:'0'}],['leading zero subject',{sub:'07'}],['numeric subject',{sub:7}],
  ['boolean subject',{sub:true}],['oversized subject',{sub:'9'.repeat(16)}],
  ['access credential claim',{access_token:'synthetic-api-canary'}],
  ['refresh credential claim',{refresh_token:'synthetic-refresh-canary'}],
]) await rejects(name,async()=>verify(await sign({...claims,...patch}),options));
for(const [name,patch] of [
  ['unsigned algorithm',{alg:'none'}],['HMAC confusion',{alg:'HS256'}],['wrong type',{typ:'at+jwt'}],
  ['missing key id',{kid:undefined}],['wrong key id',{kid:'other'}],
  ['remote JWK URL',{jku:'https://attacker.invalid'}],['remote certificate URL',{x5u:'https://attacker.invalid'}],
  ['embedded JWK',{jwk:key}],['certificate chain',{x5c:[]}],['critical headers',{crit:[]}],['unencoded payload',{b64:false}],
]) await rejects(name,async()=>verify(await sign(claims,{...header,...patch}),options));
await rejects('wrong expected account',()=>verify(assertion,{...options,expectedSubject:'8'}));
await rejects('access token in assertion slot',()=>verify('civitai_synthetic_api_canary',options));
await rejects('oversized assertion',()=>verify('a'.repeat(8193),options));
await rejects('malformed JWT',()=>verify('x.y.z',options));
await rejects('missing trusted challenge',()=>verify(assertion,{...options,nonce:''}));
await rejects('invalid trusted clock',()=>verify(assertion,{...options,now:NaN}));
await rejects('duplicated key id',()=>verify(assertion,{...options,jwks:{keys:[key,key]}}));
await rejects('private key in provider keyset',()=>verify(assertion,{...options,jwks:{keys:[{...key,d:'synthetic'}]}}));
await rejects('encryption key',()=>verify(assertion,{...options,jwks:{keys:[{...key,use:'enc'}]}}));
await rejects('sign-only provider key',()=>verify(assertion,{...options,jwks:{keys:[{...key,key_ops:['sign']}]}}));
await rejects('wrong curve',()=>verify(assertion,{...options,jwks:{keys:[{...key,crv:'P-384'}]}}));
const attacker=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
await rejects('attacker signature',async()=>verify(await sign(claims,header,attacker.privateKey),options));
const changed=assertion.split('.');changed[1]=b64(encoder.encode(JSON.stringify({...claims,sub:'8'})));
await rejects('signed subject tampering',()=>verify(changed.join('.'),options));
await rejects('padded noncanonical base64',()=>verify(assertion+'=',options));
let fetches=0, clock=now*1000;
const trusted=createCivitaiIdentityVerifier({clock:()=>clock,fetch:async(url,request)=>{
  fetches++;assert.equal(url,CIVITAI_JWKS_URL);assert.equal(request.redirect,'error');
  assert.deepEqual(request.headers,{Accept:'application/json'});
  return Response.json({keys:[key]});
}});
await rejects('opaque access token causes no key fetch',()=>trusted('civitai_synthetic_api_canary',options));
assert.equal(fetches,0);
await check('concurrent verification coalesces key fetches',async()=>{
  const results=await Promise.all(Array.from({length:16},()=>trusted(assertion,options)));
  assert.equal(fetches,1);assert(results.every(result=>result.subject==='7'));
});
await rejects('unknown kid cannot force an immediate key fetch',async()=>trusted(await sign(claims,{...header,kid:'new'}),options));
assert.equal(fetches,1);
await check('key cache expires on fixed five-minute schedule',async()=>{
  clock+=300000;await trusted(assertion,options);assert.equal(fetches,2);
});
for(const [name,response] of [
  ['redirect',()=>new Response(null,{status:302,headers:{Location:'https://attacker.invalid'}})],
  ['upstream failure',()=>new Response(null,{status:503})],
  ['oversized keyset',()=>new Response(' '.repeat(32769))],
  ['malformed keyset',()=>Response.json({keys:'invalid'})],
  ['private material in nonmatching key',()=>Response.json({keys:[key,{kid:'other',d:'synthetic'}]})],
]) await check(name+' is provider outage, never revoked login',async()=>{
  const service=createCivitaiIdentityVerifier({clock:()=>clock,fetch:async()=>response()});
  await assert.rejects(()=>service(assertion,options),{message:'IDENTITY_PROVIDER_UNAVAILABLE'});
});
await check('provider error is sanitized and retry work bounded',async()=>{
  let attempts=0, available=false;
  const service=createCivitaiIdentityVerifier({clock:()=>clock,fetch:async()=>{
    attempts++;if(!available)throw new Error('synthetic-private-error-canary');
    return Response.json({keys:[key]});
  }});
  for(let i=0;i<3;i++)await assert.rejects(()=>service(assertion,options),{message:'IDENTITY_PROVIDER_UNAVAILABLE'});
  assert.equal(attempts,1);
  available=true;clock+=10000;await service(assertion,options);assert.equal(attempts,2);
});
console.log(JSON.stringify({passed,failed:0,realEphemeralSignatures:true,liveProviderRequests:0,scope:'Verifier-only suite; exchange/session and live provider evidence are reported separately'}));
