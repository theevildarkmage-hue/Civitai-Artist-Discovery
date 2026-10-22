// Undeployed Edge Function. No SDK dependency and no browser OAuth transport.
export function createCommunityHandler({fetch:transport=globalThis.fetch, ipLimit=120,
  ipWindowMs=60000, maxConcurrent=16, ownerOnly=false} = {}) {
const fetch=transport;
const ingress=new Map<string,{started:number,count:number}>();
let active=0;
const messages: Record<string, string> = {
  INVALID_ARTWORK:"Artwork unavailable or ineligible. Use a public Civitai image URL or numeric image ID; check that it opens on Civitai.",
  UNVERIFIED_OWNER:"We couldn't verify this artwork's creator with Civitai. Please try another link.",
  SELF_RECOMMENDATION:"You can't recommend your own artwork. Let's spotlight someone else!",
  MEMBER_QUOTA:"You've used all three recommendations for this week. Come back Monday!",
  ARTIST_QUOTA:"This artist has already been featured three times this week. Pick another artist!",
  DUPLICATE:"This artwork has already been recommended. Great taste, though!",
  AUTH_REQUIRED:"Sign in to Civitai to recommend artwork.",
  AUTH_EXPIRED:"Your Civitai connection has expired. Please sign in again.",
  UNAVAILABLE:"The community service is temporarily unavailable. Your personal galleries still work.",
  RATE_LIMITED:"Please wait a moment before trying again.",
  FORBIDDEN:"This community operation is not allowed.", INVALID_REQUEST:"Please check the request and try again.",
  BETA_DISABLED:"Community Beta is temporarily disabled. Your personal galleries still work.",
  CONTENT_FILTERED:"This artwork is outside the Community Beta content policy.",
  READ_ONLY:"Community Beta is read-only. Please try submitting again later.",
};
class Failure extends Error { constructor(public code: string) { super(code); } }
const fail = (code: string): never => { throw new Failure(code); };
const status = (code: string) => ({AUTH_REQUIRED:401, AUTH_EXPIRED:401, FORBIDDEN:403,
  SELF_RECOMMENDATION:403, DUPLICATE:409, MEMBER_QUOTA:409, ARTIST_QUOTA:409,
  UNAVAILABLE:503, RATE_LIMITED:429, BETA_DISABLED:503, READ_ONLY:403, CONTENT_FILTERED:403}[code] ?? 400);
const reply = (body: unknown, code=200) => new Response(JSON.stringify(body), {
  status:code, headers:{'Content-Type':'application/json','Cache-Control':'no-store'},
});
function id(value: unknown): number {
  if (!/^[1-9][0-9]{0,14}$/.test(String(value)) || !['number','string'].includes(typeof value)) fail('INVALID_ARTWORK');
  const n = Number(value); if (!Number.isSafeInteger(n)) fail('INVALID_ARTWORK'); return n;
}
function artworkInput(value: unknown): number {
  if (typeof value === 'number') return id(value);
  if (typeof value !== 'string' || value.length>512) fail('INVALID_ARTWORK');
  const v=(value as string).trim();
  if (/^[1-9][0-9]{0,14}$/.test(v)) return id(v);
  const match=v.match(/^https:\/\/(?:www\.)?civitai\.(?:com|red)\/images\/([1-9][0-9]{0,14})\/?$/);
  if (!match) fail('INVALID_ARTWORK'); return id(match![1]);
}
async function readJSON(response: Response, max: number): Promise<any> {
  if (!response.body) fail('UNAVAILABLE');
  const reader=response.body!.getReader(), chunks:Uint8Array[]=[]; let size=0;
  const deadline=Date.now()+5000;
  try {
    while (true) {
      let timer:ReturnType<typeof setTimeout>|undefined;
      const {done,value}=await Promise.race([reader.read(),new Promise<ReadableStreamReadResult<Uint8Array>>((_,reject)=>{
        timer=setTimeout(()=>reject(new Failure('UNAVAILABLE')),Math.max(1,deadline-Date.now()));
      })]).finally(()=>clearTimeout(timer));
      if(done) break;
      size+=value.length; if(size>max) fail('INVALID_REQUEST'); chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const buffer=new Uint8Array(size); let offset=0;
  for(const chunk of chunks) {buffer.set(chunk,offset);offset+=chunk.length;}
  try {return JSON.parse(new TextDecoder().decode(buffer));} catch {return fail('INVALID_REQUEST');}
}
async function upstream(url: string, token?: string): Promise<any> {
  const response=await fetch(url, {redirect:'manual',signal:AbortSignal.timeout(8000),
    headers:{Accept:'application/json','User-Agent':'CivitaiArtistDiscovery/3.0.0-dev',...(token?{Authorization:`Bearer ${token}`}:{})}});
  if(response.status===401 || response.status===403) fail('AUTH_EXPIRED');
  if(response.status===429) fail('RATE_LIMITED');
  if(response.status===404) fail('INVALID_ARTWORK');
  if(!response.ok || response.status>=300) fail('UNAVAILABLE');
  return await readJSON(response,1048576);
}
async function rpc(name: string, args: unknown) {
  const url=Deno.env.get('SUPABASE_URL');
  const configured=Deno.env.get('SUPABASE_SECRET_KEYS');
  let key:string|undefined,modern=false;
  if(configured!==undefined) {
    // Prefer platform-injected modern keys. Invalid configuration must not
    // silently fall back to a different privileged credential.
    let keys:any; try {keys=JSON.parse(configured);} catch {return fail('UNAVAILABLE');}
    if(!keys || typeof keys!=='object' || Array.isArray(keys)
       || typeof keys.default!=='string' || !/^sb_secret_[A-Za-z0-9_-]+$/.test(keys.default)) fail('UNAVAILABLE');
    key=keys.default;modern=true;
  } else key=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  if(!url || !key) fail('UNAVAILABLE');
  const r=await fetch(`${url}/rest/v1/rpc/${name}`, {method:'POST',redirect:'error',
    signal:AbortSignal.timeout(8000),headers:{apikey:key!,...(!modern?{Authorization:`Bearer ${key}`}:{ }),
    'Content-Type':'application/json'},body:JSON.stringify(args)});
  // PostgREST returns no representation for RETURNS void. A mock JSON null is
  // not equivalent to the real HTTP 204 response from the throttle RPC.
  if(r.status===204 && name==='community_take_rate') return null;
  const data=await readJSON(r,1048576);
  if(!r.ok) {if(messages[data?.message]) fail(data.message); fail('UNAVAILABLE');}
  return data;
}
function unwrapResult(response: any): any {
  const data=response?.result?.data;
  if(data && typeof data==='object' && 'json' in data) return data.json;
  if(typeof data!=='string') return fail('INVALID_ARTWORK');
  let values:any; try {values=JSON.parse(data);} catch {return fail('INVALID_ARTWORK');}
  if(!Array.isArray(values) || values.length>50000) return fail('INVALID_ARTWORK');
  const hydrated=new Map<number,any>();
  function hydrate(pointer:any,depth=0):any {
    if(depth>100) return fail('INVALID_ARTWORK');
    if(!Number.isInteger(pointer)) return pointer;
    if(pointer<0) return null;
    if(pointer>=values.length) return fail('INVALID_ARTWORK');
    if(hydrated.has(pointer)) return hydrated.get(pointer);
    const value=values[pointer];
    if(value===null || typeof value!=='object') { hydrated.set(pointer,value); return value; }
    if(Array.isArray(value)) {
      if(typeof value[0]==='string') {
        if(value[0]==='Date') return value[1];
        if(value[0]==='BigInt') return id(value[1]);
        if(value[0]==='Set') return value.slice(1).map(p=>hydrate(p,depth+1));
        return fail('INVALID_ARTWORK');
      }
      const out:any[]=[]; hydrated.set(pointer,out);
      for(const item of value) out.push(hydrate(item,depth+1)); return out;
    }
    const out=Object.create(null); hydrated.set(pointer,out);
    for(const [key,p] of Object.entries(value)) out[key]=hydrate(p,depth+1); return out;
  }
  return hydrate(0);
}
async function artwork(image: number, token: string): Promise<number> {
  const list=await upstream(`https://civitai.red/api/v1/images?imageId=${image}&limit=1&browsingLevel=31`,token);
  const item=list?.items?.[0];
  if(!Array.isArray(list?.items) || list.items.length!==1 || item?.id!==image || item?.type!=='image') fail('INVALID_ARTWORK');
  const input=encodeURIComponent(JSON.stringify({json:{id:image}}));
  const response=await upstream(`https://civitai.red/api/trpc/image.get?input=${input}`,token);
  const detail=unwrapResult(response);
  if(detail?.id!==image) fail('INVALID_ARTWORK');
  if(!detail.user?.id || !detail.user?.username || detail.user.deletedAt || detail.user.username!==item.username) fail('UNVERIFIED_OWNER');
  let creator: number; try {creator=id(detail.user.id);} catch {return fail('UNVERIFIED_OWNER');}
  if(detail.availability!=='Public' || detail.ingestion!=='Scanned' || detail.needsReview!=null || detail.blockedFor) fail('INVALID_ARTWORK');
  if(!detail.publishedAt || !Number.isFinite(Date.parse(detail.publishedAt)) || Date.parse(detail.publishedAt)>Date.now()) fail('INVALID_ARTWORK');
  if(![1,2,4,8,16].includes(item.browsingLevel) || detail.nsfwLevel!==item.browsingLevel) fail('INVALID_ARTWORK');
  let media: URL; try {media=new URL(item.url);} catch {return fail('INVALID_ARTWORK');}
  if(media.protocol!=='https:' || media.hostname!=='image.civitai.com' || media.port || media.username || media.password) fail('INVALID_ARTWORK');
  const allowed=(Deno.env.get('COMMUNITY_ALLOWED_RATINGS')??'1,2').split(',').map(Number);
  if(!allowed.length || allowed.some(n=>![1,2,4,8,16].includes(n))) fail('UNAVAILABLE');
  if(!allowed.includes(item.browsingLevel)) fail('CONTENT_FILTERED');
  return creator!;
}

return async (request: Request, info?: {remoteAddr?:{hostname?:string}}) => {
  let admitted=false;
  try {
    const now=Date.now(), peer=info?.remoteAddr?.hostname || 'unknown';
    for(const [key,value] of ingress) if(now-value.started>=ipWindowMs) ingress.delete(key);
    let bucket=ingress.get(peer);
    if(!bucket) {
      if(ingress.size>=2048) fail('RATE_LIMITED');
      bucket={started:now,count:0}; ingress.set(peer,bucket);
    }
    if(++bucket.count>ipLimit) fail('RATE_LIMITED');
    // No browser OAuth transport. Peer identity comes from the runtime, never XFF.
    if(request.headers.has('Origin')) fail('FORBIDDEN');
    if(active>=maxConcurrent) fail('UNAVAILABLE');
    admitted=true; active++;
    if(request.method!=='POST' || (request.headers.get('content-type')||'').split(';',1)[0].trim().toLowerCase()!=='application/json') fail('INVALID_REQUEST');
    const auth=request.headers.get('Authorization')||'';
    if(!/^Bearer [^\s]{1,8192}$/.test(auth)) fail('AUTH_REQUIRED');
    const token=auth.slice(7);
    const length=request.headers.get('content-length');
    if(length && (!/^\d+$/.test(length) || Number(length)>8192)) fail('INVALID_REQUEST');
    const body=await readJSON(new Response(request.body),8192);
    if(!body || typeof body!=='object' || Array.isArray(body) || body.apiVersion!==1 || typeof body.operation!=='string') fail('INVALID_REQUEST');
    const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(token));
    const digest=Array.from(new Uint8Array(hash),n=>n.toString(16).padStart(2,'0')).join('');
    await rpc('community_take_rate',{p_key:`token:${digest}`});
    // No session cache: revocation and expiration take effect on the next request.
    const member=await upstream('https://auth.civitai.com/api/auth/oauth/userinfo',token);
    let memberId:number;
    try {memberId=id(member?.sub); if(member.id!=null && id(member.id)!==memberId) fail('AUTH_EXPIRED');}
    catch {return reply({apiVersion:1,code:'AUTH_EXPIRED',error:messages.AUTH_EXPIRED},401);}
    const username=member.preferred_username||member.username;
    if(typeof username!=='string' || username.length<1 || username.length>200) fail('AUTH_EXPIRED');
    await rpc('community_take_rate',{p_key:`member:${memberId}`});

    // The staging entry point fixes this option in code, never in a request.
    // Compare only fresh UserInfo identity, before any content/quota/write RPC.
    if(ownerOnly) {
      const owner=Deno.env.get('COMMUNITY_TEST_OWNER_ID');
      if(!owner || !/^[1-9][0-9]{0,14}$/.test(owner)) fail('BETA_DISABLED');
      if(String(memberId)!==owner) fail('FORBIDDEN');
    }

    const keys=Object.keys(body);
    const fields=body.operation==='recommend'?['artwork','idempotencyKey']:
      ['verify','withdraw'].includes(body.operation)?['artwork']:body.operation==='gallery'?['cursor','limit']:[];
    if(keys.some(k=>!['apiVersion','operation',...fields].includes(k))) fail('INVALID_REQUEST');
    // Server-owned configuration, read for every authenticated operation. Missing
    // or malformed state is disabled; clients cannot override it. Secret changes
    // disable submissions without an application release (propagation tested in staging).
    const state=Deno.env.get('COMMUNITY_BETA_STATE');
    const until=Deno.env.get('COMMUNITY_OWNER_TEST_EXPIRES_AT')??'';
    const remaining=Date.parse(until)-Date.now();
    const ownerWindow=ownerOnly && state==='owner_test'
      && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(until)
      && Number.isFinite(remaining) && remaining>0 && remaining<=30*60*1000;
    // In the deployed owner-only entry point, even "enabled" fails closed.
    const submissions=ownerWindow || (!ownerOnly && state==='enabled');
    const policy={available:submissions||state==='read_only',submissions};
    if(body.operation!=='status' && !policy.available) fail('BETA_DISABLED');
    if(['recommend','verify','withdraw'].includes(body.operation) && !policy.submissions) fail('READ_ONLY');
    let result:any;
    switch(body.operation) {
      case 'status': result={apiVersion:1,member:{id:memberId,username},
        quota:await rpc('community_quota',{p_member:memberId}),development:false,beta:policy}; break;
      case 'gallery': result=await rpc('community_gallery',{p_cursor:body.cursor==null?null:id(body.cursor),p_limit:body.limit??24}); break;
      case 'verify': case 'recommend': {
        const image=artworkInput(body.artwork), creator=await artwork(image,token);
        const key=body.idempotencyKey;
        if(key!==undefined && (body.operation!=='recommend' || typeof key!=='string' || !/^[A-Za-z0-9_-]{16,80}$/.test(key))) fail('INVALID_REQUEST');
        result=await rpc(key===undefined?'community_submit':'community_submit_once',{
          p_member:memberId,p_name:username,p_image:image,p_creator:creator,
          ...(key===undefined?{p_preview:body.operation==='verify'}:{p_key:key})}); break;
      }
      case 'withdraw': result=await rpc('community_withdraw',{p_member:memberId,p_image:artworkInput(body.artwork)}); break;
      default: return fail('INVALID_REQUEST');
    }
    return reply(result);
  } catch(error) {
    // Do not log Request, OAuth tokens, upstream body or exception details.
    const code=error instanceof Failure ? error.code : 'UNAVAILABLE';
    return reply({apiVersion:1,code,error:messages[code]},status(code));
  } finally { if(admitted) active--; }
};
}
