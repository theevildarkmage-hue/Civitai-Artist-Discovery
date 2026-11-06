// Discovery-only, rotating, proof-of-possession renewal. Never accepts Civitai API credentials.
const encoder=new TextEncoder();
const b64=(value:ArrayBuffer|Uint8Array)=>btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const hash=async(value:string)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),v=>v.toString(16).padStart(2,'0')).join('');
const random=()=>b64(crypto.getRandomValues(new Uint8Array(32)));
const bad=():never=>{throw Error('AUTH_EXPIRED');};
function decode(value:string){
 if(typeof value!=='string'||!/^[A-Za-z0-9_-]+$/.test(value))bad();
 const bytes=Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4)),c=>c.charCodeAt(0));
 if(b64(bytes)!==value)bad();return bytes;
}
export async function deviceKey(value:any):Promise<JsonWebKey> {
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='crv,kty,x,y'
  ||value.kty!=='EC'||value.crv!=='P-256'||decode(value.x).length!==32||decode(value.y).length!==32)bad();
 await crypto.subtle.importKey('jwk',value,{name:'ECDSA',namedCurve:'P-256'},false,['verify']);
 return {kty:'EC',crv:'P-256',x:value.x,y:value.y};
}
async function proof(key:any,signature:any,message:string) {
 if(typeof signature!=='string'||signature.length!==86)bad();
 const imported=await crypto.subtle.importKey('jwk',await deviceKey(key),{name:'ECDSA',namedCurve:'P-256'},false,['verify']);
 if(!await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},imported,decode(signature),encoder.encode(message)))bad();
}
export function createDeviceRenewal({rpc,env,clock,publicUser}:{rpc:(name:string,args:any)=>Promise<any>,env:(name:string)=>string|undefined,clock:()=>number,publicUser:(id:string)=>Promise<any>}) {
 async function derive(value:string) {
  const secret=env('DISCOVERY_IDENTITY_CHALLENGE_KEY');if(!secret)throw Error('UNAVAILABLE');
  const key=await crypto.subtle.importKey('raw',decode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  return b64(await crypto.subtle.sign('HMAC',key,encoder.encode(value)));
 }
 function response(row:any,session:string,renewal:string,member:any) {
  return {apiVersion:2,session,expiresAt:row.expiresAt,member,
   renewal:{credential:renewal,expiresAt:row.refreshExpiresAt,absoluteExpiresAt:row.absoluteExpiresAt}};
 }
 return {
  async enroll(challenge:any,challengeText:string,assertion:string,signature:any,identity:any,member:any) {
   const key=await deviceKey(challenge.devicePublicKey);
   await proof(key,signature,'discovery-enroll-v1\n'+challengeText+'\n'+await hash(assertion));
   const session='sdc_'+random(),renewal='sdr_'+random();
   const row=await rpc('community_device_enroll',{p_assertion_hash:await hash(assertion),p_nonce_hash:await hash(challenge.nonce),
    p_session_hash:await hash(session),p_refresh_hash:await hash(renewal),p_device_key:key,p_member:Number(identity.subject),
    p_username:member.username,p_proof_expires:new Date(identity.expiresAt*1000).toISOString()});
   return response(row,session,renewal,member);
  },
  async renew(body:any) {
   if(Object.keys(body).some(k=>!['apiVersion','operation','credential','requestId','attemptNonce','issuedAt','signature'].includes(k))
    ||typeof body.credential!=='string'||!/^sdr_[A-Za-z0-9_-]{43}$/.test(body.credential)
    ||!Number.isSafeInteger(body.issuedAt)
    ||typeof body.requestId!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(body.requestId)
    ||typeof body.attemptNonce!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(body.attemptNonce))bad();
   // Clock skew is recoverable; do not tell the client its device was revoked.
   if(Math.abs(Math.floor(clock()/1000)-body.issuedAt)>60)throw Error('UNAVAILABLE');
   const credentialHash=await hash(body.credential);
   const row=await rpc('community_device_lookup',{p_refresh_hash:credentialHash});
   if(row.denied)bad();
   const message='discovery-renew-v1\n'+credentialHash+'\n'+body.requestId+'\n'+body.attemptNonce+'\n'+body.issuedAt;
   await proof(row.deviceKey,body.signature,message);
   const member=await publicUser(String(row.memberId));
   const stem='discovery-rotate-v1\n'+credentialHash+'\n'+body.requestId;
   const renewal='sdr_'+await derive(stem+'\nrefresh');
   const session='sdc_'+await derive(stem+'\naccess\n'+body.attemptNonce);
   const rotated=await rpc('community_device_rotate',{p_refresh_hash:credentialHash,p_request_hash:await hash(body.requestId),
    p_attempt_hash:await hash(message),p_next_refresh_hash:await hash(renewal),p_session_hash:await hash(session),p_username:member.username});
   if(rotated.denied)bad();
   return response(rotated,session,renewal,{id:Number(row.memberId),username:member.username});
  },
 };
}
