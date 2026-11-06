import {createCommunityHandler} from './handler.ts';
import {createCivitaiIdentityVerifier} from './identity.ts';
import {createDeviceRenewal,deviceKey} from './renewal.ts';

const audience='b9e2edf5-4bf0-4381-8823-ed01603256c5';
const encoder=new TextEncoder();
const b64=(bytes:ArrayBuffer|Uint8Array)=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const random=()=>b64(crypto.getRandomValues(new Uint8Array(32)));
const hash=async(value:string)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),b=>b.toString(16).padStart(2,'0')).join('');
function failure(code:string):never {throw new Error(code);}
function decode(value:string) {
  if(!/^[A-Za-z0-9_-]+$/.test(value))failure('AUTH_EXPIRED');
  const bytes=Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4)),c=>c.charCodeAt(0));
  if(b64(bytes)!==value)failure('AUTH_EXPIRED');return bytes;
}
async function json(response:Response,max=32768) {
  if(!response.body)failure('UNAVAILABLE');
  const reader=response.body!.getReader(),chunks:Uint8Array[]=[];let size=0;
  const deadline=Date.now()+5000;
  try {while(true) {
    let timer:ReturnType<typeof setTimeout>|undefined;
    const {done,value}=await Promise.race([reader.read(),new Promise<ReadableStreamReadResult<Uint8Array>>((_,reject)=>{
      timer=setTimeout(()=>reject(new Error('UNAVAILABLE')),Math.max(1,deadline-Date.now()));
    })]).finally(()=>clearTimeout(timer));
    if(done)break;size+=value.length;if(size>max)failure('INVALID_REQUEST');chunks.push(value);
  }} finally {await reader.cancel();}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
const reply=(body:unknown,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store'}});

export function createIdentityCommunityHandler({fetch:transport=globalThis.fetch,
  env=(name:string)=>Deno.env.get(name),clock=()=>Date.now()}={}) {
  const verify=createCivitaiIdentityVerifier({fetch:transport,clock});
  let active=0;
  async function rpc(name:string,args:unknown) {
    const url=env('SUPABASE_URL');let key=env('SUPABASE_SERVICE_ROLE_KEY'),modern=false;
    const configured=env('SUPABASE_SECRET_KEYS');
    if(configured!==undefined){key=JSON.parse(configured)?.default;modern=true;if(!/^sb_secret_[A-Za-z0-9_-]+$/.test(key??''))failure('UNAVAILABLE');}
    if(!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(url??'')||!key)failure('UNAVAILABLE');
    const response=await transport(url+'/rest/v1/rpc/'+name,{method:'POST',redirect:'error',signal:AbortSignal.timeout(8000),
      headers:{apikey:key!,...(!modern?{Authorization:'Bearer '+key}:{}),'Content-Type':'application/json'},body:JSON.stringify(args)});
    const body=await json(response);
    if(!response.ok)failure(body?.message==='AUTH_EXPIRED'?'AUTH_EXPIRED':'UNAVAILABLE');return body;
  }
  async function challengeKey() {
    const key=env('DISCOVERY_IDENTITY_CHALLENGE_KEY');
    if(!key||!/^[A-Za-z0-9_-]{43}$/.test(key))failure('UNAVAILABLE');
    let raw:Uint8Array<ArrayBuffer>;try {raw=decode(key);}catch {return failure('UNAVAILABLE');}
    return crypto.subtle.importKey('raw',raw,{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);
  }
  async function makeChallenge(publicKey?:unknown) {
    const devicePublicKey=publicKey===undefined?undefined:await deviceKey(publicKey);
    const issued=Math.floor(clock()/1000);
    const body=b64(encoder.encode(JSON.stringify({purpose:'discovery-identity-v1',aud:audience,nonce:random(),iat:issued,exp:issued+300,...(devicePublicKey?{devicePublicKey}:{})})));
    return {apiVersion:2,challenge:body+'.'+b64(await crypto.subtle.sign('HMAC',await challengeKey(),encoder.encode(body))),
      nonce:JSON.parse(new TextDecoder().decode(decode(body))).nonce,audience,expiresAt:issued+300,...(devicePublicKey?{devicePublicKey}:{})};
  }
  async function readChallenge(value:unknown) {
    if(typeof value!=='string'||value.length>1024)failure('AUTH_EXPIRED');
    const parts=value.split('.');if(parts.length!==2)failure('AUTH_EXPIRED');
    if(!await crypto.subtle.verify('HMAC',await challengeKey(),decode(parts[1]),encoder.encode(parts[0])))failure('AUTH_EXPIRED');
    const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(decode(parts[0]))),now=Math.floor(clock()/1000);
    if(data.purpose!=='discovery-identity-v1'||data.aud!==audience||typeof data.nonce!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(data.nonce)
      ||!Number.isSafeInteger(data.iat)||data.iat>now+30||data.exp!==data.iat+300||data.exp<=now)failure('AUTH_EXPIRED');return data;
  }
  async function publicUser(subject:string) {
    const response=await transport('https://civitai.red/api/v1/users?ids='+subject,{redirect:'error',signal:AbortSignal.timeout(8000),headers:{Accept:'application/json'}});
    if(!response.ok)failure('UNAVAILABLE');const users=await json(response);
    const matches=users?.items?.filter((user:any)=>user?.id===Number(subject));
    if(!Array.isArray(matches)||matches.length!==1||typeof matches[0].username!=='string'||matches[0].username.length<1||matches[0].username.length>200)failure('UNAVAILABLE');
    return matches[0];
  }
  const renewal=createDeviceRenewal({rpc,env,clock,publicUser});
  const community=createCommunityHandler({fetch:transport,invitationOnly:true,recommendationList:true,
    authenticate:async(token:string)=>{
      if(!/^sdc_[A-Za-z0-9_-]{43}$/.test(token))failure('AUTH_EXPIRED');
      return rpc('community_identity_resolve',{p_session_hash:await hash(token)});
    },publicArtwork:true});
  return async(request:Request)=>{
    let admitted=false;
    try {
      if(request.headers.has('Origin'))failure('FORBIDDEN');
      if(request.method!=='POST'||request.headers.get('content-type')?.split(';')[0].trim()!=='application/json')failure('INVALID_REQUEST');
      if(active>=16)failure('UNAVAILABLE');active++;admitted=true;
      const size=request.headers.get('content-length');if(size&&(!/^\d+$/.test(size)||Number(size)>131072))failure('INVALID_REQUEST');
      const body=await json(new Response(request.body),131072);
      if(!body||typeof body!=='object'||Array.isArray(body))failure('INVALID_REQUEST');
      if(body.operation!=='gallery'&&JSON.stringify(body).length>16384)failure('INVALID_REQUEST');
      if(body.apiVersion===2) {
        // Identity exchange endpoints never accept an API/session bearer header.
        if(request.headers.has('Authorization'))failure('AUTH_EXPIRED');
        if(body.operation==='identityRenew')return reply(await renewal.renew(body));
        if(env('DISCOVERY_REGISTRY_STATE')!=='enabled'&&!['enabled','read_only'].includes(env('COMMUNITY_BETA_STATE')??''))
          return reply({apiVersion:2,available:false});
        if(body.operation==='identityChallenge'&&Object.keys(body).every(k=>['apiVersion','operation','devicePublicKey'].includes(k)))return reply(await makeChallenge(body.devicePublicKey));
        if(body.operation!=='identityExchange'||Object.keys(body).some(k=>!['apiVersion','operation','challenge','identityAssertion','deviceSignature'].includes(k)))failure('INVALID_REQUEST');
        const challenge=await readChallenge(body.challenge);
        const identity=await verify(body.identityAssertion,{audience,nonce:challenge.nonce});
        if(identity.issuedAt<challenge.iat-30)failure('AUTH_EXPIRED');
        const user=await publicUser(identity.subject);
        const matches=[user];
        if(challenge.devicePublicKey)return reply(await renewal.enroll(challenge,body.challenge,body.identityAssertion,body.deviceSignature,identity,{id:Number(identity.subject),username:user.username}));
        const session='sdc_'+random();
        const result=await rpc('community_identity_exchange',{p_assertion_hash:await hash(body.identityAssertion),p_nonce_hash:await hash(challenge.nonce),
          p_session_hash:await hash(session),p_member:Number(identity.subject),p_username:matches[0].username,p_proof_expires:new Date(identity.expiresAt*1000).toISOString()});
        return reply({apiVersion:2,session,expiresAt:result.expiresAt,member:{id:Number(identity.subject),username:matches[0].username}});
      }
      if(body.apiVersion!==1||!/^Bearer sdc_[A-Za-z0-9_-]{43}$/.test(request.headers.get('Authorization')??''))failure('AUTH_EXPIRED');
      if(body.operation==='identityLogout') {
        if(Object.keys(body).some(k=>!['apiVersion','operation'].includes(k)))failure('INVALID_REQUEST');
        return reply({apiVersion:1,...await rpc('community_identity_revoke',{p_session_hash:await hash(request.headers.get('Authorization')!.slice(7))})});
      }
      return await community(new Request(request.url,{method:'POST',headers:request.headers,body:JSON.stringify(body)}));
    } catch(error) {
      const message=error instanceof Error?error.message:'';
      const code=message==='INVALID_IDENTITY_ASSERTION'?'AUTH_EXPIRED':['AUTH_EXPIRED','FORBIDDEN','INVALID_REQUEST'].includes(message)?message:'UNAVAILABLE';
      return reply({apiVersion:1,code,message:code==='AUTH_EXPIRED'?'Please sign in to Civitai again.':'The community service is temporarily unavailable.'},code==='AUTH_EXPIRED'?401:code==='FORBIDDEN'?403:code==='INVALID_REQUEST'?400:503);
    } finally {if(admitted)active--;}
  };
}
