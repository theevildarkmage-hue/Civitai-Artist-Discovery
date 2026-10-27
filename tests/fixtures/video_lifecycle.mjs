import assert from 'node:assert/strict';
globalThis.matchMedia = () => ({matches:false, addEventListener(){}});
globalThis.document = new EventTarget();
globalThis.window = new EventTarget();
globalThis.IntersectionObserver = class { observe(){} unobserve(){} };
const {createMediaLifecycle, VIDEO_POLICY} = await import('../../static/ui/video.js');

class Clock {
  time=0; next=0; tasks=new Map();
  now=()=>this.time;
  setTimeout=(fn,ms)=>{const id=++this.next;this.tasks.set(id,{at:this.time+ms,fn});return id};
  clearTimeout=id=>this.tasks.delete(id);
  async advance(ms){
    const end=this.time+ms;
    for(;;){
      const found=[...this.tasks].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!found)break;
      this.tasks.delete(found[0]);this.time=found[1].at;found[1].fn();await Promise.resolve();await Promise.resolve();
    }
    this.time=end;await Promise.resolve();await Promise.resolve();
  }
}
class Video extends EventTarget {
  currentTime=0; playbackRate=1; volume=1; muted=true; paused=true; readyState=0; duration=60; seeking=false;
  buffer=0; src=''; removed=false; error=null; playError=null; pending=null;
  get buffered(){return {length:this.buffer>0?1:0,end:()=>this.buffer,start:()=>0}}
  get seekable(){return {length:this.readyState?1:0,end:()=>this.duration,start:()=>0}}
  play(){this.plays=(this.plays||0)+1;this.paused=false;this.dispatchEvent(new Event('play'));return this.pending || (this.playError?Promise.reject(this.playError):Promise.resolve())}
  pause(){this.paused=true;this.dispatchEvent(new Event('pause'))}
  removeAttribute(name){if(name==='src')this.src=''}
  load(){}
  remove(){this.removed=true}
}
function fixture({card=true,muted,budget=new Map(),make=()=>new Video(),clock=new Clock(),reserve=at=>at}={}){
  const videos=[],renders=[],events=[];
  let eligible=true,online=true;
  const controller=createMediaLifecycle({card,...(muted===undefined ? {} : {muted}),clock,budget,policy:VIDEO_POLICY,...(reserve ? {reserve} : {}),
    createVideo:()=>{const v=make();videos.push(v);return v},mount(){},
    eligible:()=>eligible,online:()=>online,render:v=>renders.push(v),diagnose:(...args)=>events.push(args)});
  controller.setItem({id:'1',source:'https://image.civitai.com/fixture.mp4'});
  return {controller,clock,videos,renders,events,setEligible:v=>eligible=v,setOnline:v=>online=v};
}

// Initial timeout: one card reload, then a manual action. Never overlapping old sources.
{
 const f=fixture();f.controller.start();await f.clock.advance(15000);
 assert.equal(f.controller.state,'recovering');assert.equal(f.videos[0].src,'');
 await f.clock.advance(3000);assert.equal(f.videos.length,2);assert(f.videos[0].removed);
 await f.clock.advance(15000);assert.equal(f.controller.state,'failed');
 await f.clock.advance(60000);assert.equal(f.videos.length,2);
 f.controller.retry();assert.equal(f.videos.length,3);f.controller.release();
}
// A slow functioning load, including beyond the soft 45s boundary, must not redownload.
{
 const f=fixture();f.controller.start();const v=f.videos[0];
 for(let i=0;i<60;i++){v.buffer+=0.25;await f.clock.advance(1000)}
 assert.equal(f.videos.length,1);assert.notEqual(f.controller.state,'recovering');
 v.currentTime=1;await f.clock.advance(1000);assert.equal(f.controller.state,'playing');f.controller.release();
}
// Playback really advances for five seconds; buffering grows, recovers, then genuinely stalls.
{
 const f=fixture();f.controller.start();const v=f.videos[0];v.networkState=2;
 for(let i=0;i<60;i++){v.dispatchEvent(new Event('progress'));await f.clock.advance(1000)}
 assert.equal(f.videos.length,1);assert.equal(v.buffered.length,0);f.controller.release();
}
// A canceled seek preserves the original resume target; an unseekable reload offers an explicit restart.
{
 const f=fixture();f.controller.start();const first=f.videos[0];first.currentTime=8;
 await f.clock.advance(1000);await f.clock.advance(18000);
 assert.equal(f.videos.length,2);f.controller.cancel('hidden');f.controller.start();
 const resumed=f.videos[2];resumed.readyState=1;resumed.dispatchEvent(new Event('loadedmetadata'));
 assert.equal(resumed.currentTime,8);f.controller.release();
}
{
 const f=fixture();f.controller.start();f.videos[0].currentTime=8;
 await f.clock.advance(19000);await f.clock.advance(15000);
 assert.equal(f.controller.state,'failed');f.controller.retry();
 assert.equal(f.videos.at(-1).currentTime,0);f.controller.release();
}
// Playback really advances for five seconds; buffering grows, recovers, then genuinely stalls.
{
 const f=fixture();f.controller.start();const v=f.videos[0];v.readyState=3;
 for(let i=1;i<=5;i++){v.currentTime=i;await f.clock.advance(1000)}
 for(let i=0;i<10;i++){v.buffer+=1;await f.clock.advance(1000)}
 assert.equal(f.videos.length,1);v.currentTime=6;await f.clock.advance(1000);
 assert.equal(f.controller.state,'playing');v.volume=.3;v.playbackRate=1.5;
 await f.clock.advance(15000);assert.equal(f.controller.state,'recovering');
 await f.clock.advance(3000);const restored=f.videos[1];restored.readyState=1;
 restored.dispatchEvent(new Event('loadedmetadata'));await Promise.resolve();
 assert.equal(restored.currentTime,6);assert.equal(restored.volume,.3);assert.equal(restored.playbackRate,1.5);assert.equal(restored.muted,true);
 f.controller.release();
}
// Every invalidating action defeats pending backoff, and late old element events.
for(const action of ['pause','offscreen','carousel','close','hidden','replace']){
 const f=fixture();f.controller.start();await f.clock.advance(15000);const old=f.videos[0];
 if(action==='pause')f.controller.pause();
 else if(action==='carousel')f.controller.setItem({id:'2',source:'other'});
 else if(action==='close'||action==='replace')f.controller.release();
 else {f.setEligible(false);f.controller.cancel(action)}
 old.dispatchEvent(new Event('playing'));old.dispatchEvent(new Event('error'));
 await f.clock.advance(60000);assert.equal(f.videos.length,1,action);assert.equal(old.src,'');
}
// Pending play promises cannot revive canceled players.
{
 let resolve;const f=fixture({make:()=>{const v=new Video();v.pending=new Promise(r=>resolve=r);return v}});
 f.controller.start();f.controller.pause();resolve();await Promise.resolve();await Promise.resolve();
 assert.equal(f.controller.state,'user-paused');assert.equal(f.videos[0].src,'');
}
// Details honor the sound preference; cards always start muted.
{
 const detail=fixture({card:false,muted:false});detail.controller.start();
 assert.equal(detail.videos[0].muted,false);detail.controller.release();
 const card=fixture({card:true,muted:false});card.controller.start();
 assert.equal(card.videos[0].muted,true);card.controller.release();
 const quiet=fixture({card:false,muted:true});quiet.controller.start();
 assert.equal(quiet.videos[0].muted,true);quiet.controller.release();
}
// A browser that refuses audible playback gets muted playback, not a stop.
{
 const f=fixture({card:false,muted:false,make:()=>{const v=new Video();v.play=function(){this.plays=(this.plays||0)+1;
  if(!this.muted)return Promise.reject({name:'NotAllowedError'});this.paused=false;return Promise.resolve()};return v}});
 f.controller.start();f.videos[0].dispatchEvent(new Event('loadedmetadata'));await f.clock.advance(1000);
 assert.equal(f.videos.length,1);assert.equal(f.videos[0].muted,true);assert.equal(f.videos[0].plays,2);
 assert.notEqual(f.controller.state,'awaiting-gesture');f.controller.release();
}
// Browser policy denial isn't a network retry; unsupported media isn't blindly reloaded.
for(const name of ['NotAllowedError','NotSupportedError']){
 const f=fixture({make:()=>{const v=new Video();v.playError={name};return v}});
 f.controller.start();await f.clock.advance(60000);assert.equal(f.videos.length,1);
 assert.equal(f.controller.state,name==='NotAllowedError'?'awaiting-gesture':'failed');f.controller.release();
}
// Shared budget survives separate card/detail controllers, visibility, and brief playback.
{
 const budget=new Map();const a=fixture({budget});a.controller.start();await a.clock.advance(18000);a.controller.release();
 const b=fixture({budget,card:false});b.controller.start();await b.clock.advance(25000);
 assert.equal(b.videos.length,2);await b.clock.advance(60000);assert.equal(b.videos.length,2);assert.equal(b.controller.state,'failed');
}
// Details get two automatic reloads, then a manual action.
{
 const f=fixture({card:false});f.controller.start();await f.clock.advance(18000);
 assert.equal(f.videos.length,2);await f.clock.advance(25000);assert.equal(f.videos.length,3);
 await f.clock.advance(15000);assert.equal(f.controller.state,'failed');f.controller.release();
}
// The production recovery scheduler separates simultaneous attempts by one second.
{
 const clock=new Clock();const a=fixture({clock,reserve:null}),b=fixture({clock,reserve:null});
 a.controller.start();b.controller.start();await clock.advance(18000);
 assert.equal(a.videos.length,2);assert.equal(b.videos.length,1);
 await clock.advance(1000);assert.equal(b.videos.length,2);a.controller.release();b.controller.release();
}
// Offline events do not consume a reload; return online respects eligibility.
{
 const f=fixture();f.setOnline(false);f.controller.start();await f.clock.advance(15000);
 assert.equal(f.controller.state,'offline');f.setOnline(true);f.controller.online();await f.clock.advance(3000);assert.equal(f.videos.length,2);f.controller.release();
}
// Native detail pause keeps its element and intent; resume uses the same source.
{
 const f=fixture({card:false});f.controller.start();await Promise.resolve();const v=f.videos[0];v.pause();
 await f.clock.advance(60000);assert.equal(f.controller.state,'user-paused');assert.equal(f.videos.length,1);
 v.play();await f.clock.advance(1000);assert.equal(f.videos.length,1);f.controller.release();
}
console.log('video lifecycle: slow loads, natural recovery, true stalls, budgets, restoration, cancellation and native controls passed');
