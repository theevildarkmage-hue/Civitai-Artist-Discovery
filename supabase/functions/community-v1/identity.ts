// Identity-only verification boundary imported by the candidate production index.
// nonce/audience/JWKS must come from trusted server configuration and a stored
// challenge, never from unverified request claims. Durable challenge consumption
// and session issuance belong to the exchange layer, not this verifier.
export const CIVITAI_ISSUER = 'https://auth.civitai.com';
export const CIVITAI_JWKS_URL = CIVITAI_ISSUER+'/.well-known/jwks.json';
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', {fatal:true});

function invalid(): never { throw new Error('INVALID_IDENTITY_ASSERTION'); }
function object(value: unknown): Record<string, any> {
  if(value===null || typeof value!=='object' || Array.isArray(value)) invalid();
  return value as Record<string, any>;
}
function decode(value: string): Uint8Array<ArrayBuffer> {
  if(!value || !/^[A-Za-z0-9_-]+$/.test(value)) invalid();
  const binary=atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4));
  const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
  const canonical=btoa(binary).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  if(canonical!==value) invalid();
  return bytes;
}

export async function verifyCivitaiIdentityAssertion(assertion: string, options: {
  jwks: unknown; audience: string; nonce: string; now?: number; expectedSubject?: string;
}): Promise<{subject:string; issuedAt:number; expiresAt:number}> {
  try {
    const {jwks,audience,nonce,expectedSubject}=options;
    const now=options.now ?? Math.floor(Date.now()/1000);
    if(typeof assertion!=='string' || assertion.length<40 || assertion.length>8192
       || typeof audience!=='string' || !audience || audience.length>200
       || typeof nonce!=='string' || !/^[A-Za-z0-9_-]{32,128}$/.test(nonce)
       || !Number.isSafeInteger(now) || now<0) invalid();
    const parts=assertion.split('.');
    if(parts.length!==3) invalid();
    const header=object(JSON.parse(decoder.decode(decode(parts[0]))));
    const claims=object(JSON.parse(decoder.decode(decode(parts[1]))));
    const signature=decode(parts[2]);
    if(header.alg!=='ES256' || header.typ!=='JWT' || typeof header.kid!=='string'
       || !header.kid || header.kid.length>128 || signature.length!==64) invalid();
    if(['jku','x5u','jwk','x5c','crit','b64'].some(k=>k in header)) invalid();
    if(claims.iss!==CIVITAI_ISSUER || claims.aud!==audience || claims.nonce!==nonce
       || (claims.azp!==undefined && claims.azp!==audience)) invalid();
    if(typeof claims.sub!=='string' || !/^[1-9][0-9]{0,14}$/.test(claims.sub)
       || !Number.isSafeInteger(Number(claims.sub))
       || (expectedSubject!==undefined && claims.sub!==expectedSubject)) invalid();
    if(!Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.exp)
       || claims.iat<0 || claims.exp<=claims.iat || claims.exp-claims.iat>3600
       || claims.iat>now+30 || claims.exp<=now) invalid();
    if(claims.nbf!==undefined && (!Number.isSafeInteger(claims.nbf) || claims.nbf>now+30)) invalid();
    if(claims.auth_time!==undefined && (!Number.isSafeInteger(claims.auth_time)
       || claims.auth_time<0 || claims.auth_time>now+30)) invalid();
    if(['access_token','refresh_token','accessToken','refreshToken'].some(k=>k in claims)) invalid();
    const keys=object(jwks).keys;
    if(!Array.isArray(keys) || keys.length>16) invalid();
    const matching=keys.filter(key=>key?.kid===header.kid);
    if(matching.length!==1) invalid();
    const key=object(matching[0]);
    if(key.kty!=='EC' || key.crv!=='P-256' || key.d!==undefined
       || (key.use!==undefined && key.use!=='sig')
       || (key.alg!==undefined && key.alg!=='ES256')
       || (key.key_ops!==undefined && (!Array.isArray(key.key_ops)
         || key.key_ops.length!==1 || key.key_ops[0]!=='verify'))) invalid();
    const imported=await crypto.subtle.importKey('jwk',key as JsonWebKey,
      {name:'ECDSA',namedCurve:'P-256'},false,['verify']);
    if(!await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},imported,signature,
      encoder.encode(parts[0]+'.'+parts[1]))) invalid();
    // Return only verified identity/time fields; no email, arbitrary profile
    // claims, nonce or credential material escapes the verification boundary.
    return {subject:claims.sub,issuedAt:claims.iat,expiresAt:claims.exp};
  } catch { return invalid(); }
}

// No token-derived URLs or API credentials are accepted by this fetch boundary.
// Cache lifetime is fixed locally, not controlled by response headers. Unknown
// kids fail closed until the next scheduled cache refresh instead of triggering
// arbitrary upstream work for each attacker-supplied kid.
export function createCivitaiIdentityVerifier({fetch:transport=globalThis.fetch,
  clock=()=>Date.now()} = {}) {
  let cached:unknown, validUntil=0, retryAfter=0, pending:Promise<unknown>|undefined;
  async function loadKeys(): Promise<unknown> {
    const response=await transport(CIVITAI_JWKS_URL,{redirect:'error',
      signal:AbortSignal.timeout(8000),headers:{Accept:'application/json'}});
    if(response.status!==200 || !response.body) invalid();
    const reader=response.body.getReader(), chunks:Uint8Array[]=[];
    let size=0;
    const deadline=Date.now()+5000;
    try {
      while(true) {
        let timer:ReturnType<typeof setTimeout>|undefined;
        const {value,done}=await Promise.race([reader.read(),
          new Promise<ReadableStreamReadResult<Uint8Array>>((_,reject)=>{
            timer=setTimeout(()=>reject(new Error('INVALID_IDENTITY_ASSERTION')),
              Math.max(1,deadline-Date.now()));
          })]).finally(()=>clearTimeout(timer));
        if(done) break;
        size+=value.length;
        if(size>32768) invalid();
        chunks.push(value);
      }
    } finally {await reader.cancel();}
    const bytes=new Uint8Array(size);let offset=0;
    for(const chunk of chunks) {bytes.set(chunk,offset);offset+=chunk.length;}
    const result=object(JSON.parse(decoder.decode(bytes)));
    if(!Array.isArray(result.keys) || result.keys.length<1 || result.keys.length>16) invalid();
    // Never cache private material, even under a nonmatching key id.
    for(const key of result.keys) if(object(key).d!==undefined) invalid();
    cached=result;validUntil=clock()+300000;
    return result;
  }
  return async (assertion:string, context:{audience:string;nonce:string;expectedSubject?:string}) => {
    try {
      // Reject opaque credentials before even making a public-key request.
      if(typeof assertion!=='string' || assertion.length>8192
        || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(assertion)) invalid();
      let jwks=cached;
      if(!cached || clock()>=validUntil) {
        if(clock()<retryAfter) throw new Error('IDENTITY_PROVIDER_UNAVAILABLE');
        pending ??= loadKeys().catch(()=>{
          retryAfter=clock()+10000;
          throw new Error('IDENTITY_PROVIDER_UNAVAILABLE');
        }).finally(()=>{pending=undefined;});
        jwks=await pending;
      }
      return await verifyCivitaiIdentityAssertion(assertion,{...context,jwks,
        now:Math.floor(clock()/1000)});
    } catch(error) {
      // Provider outages must not be reported as a revoked user credential.
      if(error instanceof Error && error.message==='IDENTITY_PROVIDER_UNAVAILABLE')
        throw new Error('IDENTITY_PROVIDER_UNAVAILABLE');
      return invalid();
    }
  };
}
