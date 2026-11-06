// Serialize preference writes, coalesce rapid clicks and ignore obsolete replies.
export function createSelectionQueue({read, preview, save, settle, failure, delay=250}) {
  let desired, saved, revision=0, running=false, timer=null, waiters=[];
  function schedule() { clearTimeout(timer); timer=setTimeout(flush,delay); }
  async function flush() {
    clearTimeout(timer);timer=null;
    if(running)return;
    running=true;
    let result=null,error=null;
    while(true) {
      const sent=revision, value=[...desired];
      try { result=await save(value);saved=[...result.browsingLevels];error=null; }
      catch(problem) {error=problem;}
      if(sent!==revision)continue;
      if(error)desired=[...saved];
      preview(desired,result,error);
      break;
    }
    running=false;
    const completed=waiters,completedRevision=revision;waiters=[];
    try {
      if(error)failure(error);
      await settle(result,error,()=>revision===completedRevision);
      completed.forEach(resolve=>resolve(!error));
    } catch(problem) {failure(problem);completed.forEach(resolve=>resolve(false));}
  }
  return {
    choose(value) {
      if(!running&&!timer)saved=[...read()];
      desired=[...value];++revision;preview(desired,null,null);
      const promise=new Promise(resolve=>waiters.push(resolve));if(!running)schedule();return promise;
    },
    get pending(){return running||timer!==null;},
  };
}
