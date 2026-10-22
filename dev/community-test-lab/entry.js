// Injected only by the optional development wrapper; absent from static packaging.
async function mount() {
  const host=document.querySelector('#developmentDiagnostics');
  if(!host) return setTimeout(mount,200);
  const lab=document.createElement('details');lab.id='communityTestLab';
  const style=document.createElement('link');style.rel='stylesheet';
  style.href='/__dev/community-test-lab/entry.css';document.head.append(style);
  const summary=document.createElement('summary');summary.textContent='Community Test Lab';lab.append(summary);
  const note=document.createElement('p');note.textContent='Simulated members mapped to verified public Civitai uploaders. These are not their OAuth sessions. Each window uses an isolated local profile; this normal account stays unchanged.';lab.append(note);
  const list=document.createElement('div');lab.append(list);
  const inspector=document.createElement('form');inspector.innerHTML='<label>Ownership Verification <select aria-label="Simulated member"></select></label> <input aria-label="Artwork URL or ID" placeholder="Civitai artwork URL or ID" required> <button type="submit">Verify ownership</button><pre role="status" style="white-space:pre-wrap"></pre>';lab.append(inspector);
  host.append(lab);let loaded=false;
  const request=async(path,body)=>{
    const response=await fetch('/__dev/community-test-lab/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    if(!response.ok)throw Error('Local Test Lab unavailable. Check its development service.');return response.json();
  };
  async function load(){
    if(loaded)return;
    try{
      const members=await request('status');loaded=true;
      for(const member of members){
        const row=document.createElement('div');row.style.cssText='display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 0;border-bottom:1px solid var(--border)';
        if(member.creator.avatar){const image=document.createElement('img');image.src=member.creator.avatar;image.alt='';image.width=image.height=32;image.style.borderRadius='50%';row.append(image);}
        const label=document.createElement('span');label.textContent=member.creator.username+' - Simulated member - '+(member.quota?member.quota.used+'/3 used, '+member.quota.remaining+' remaining':'service unavailable');row.append(label);
        const open=document.createElement('button');open.type='button';open.textContent='Open isolated session';
        open.onclick=async()=>{const tab=window.open('about:blank','_blank');try{const result=await request('open',{index:member.index});tab.location=result.url;}catch(error){tab?.close();inspector.querySelector('pre').textContent=error.message;}};row.append(open);
        const art=document.createElement('details'),title=document.createElement('summary');title.textContent='Available real artwork';art.append(title);
        for(const image of member.artworks){const link=document.createElement('a');link.href=image.url;link.target='_blank';link.rel='noopener';link.textContent=image.id+' (rating '+image.rating+')';link.style.marginRight='12px';art.append(link);}row.append(art);list.append(row);
        const option=document.createElement('option');option.value=member.index;option.textContent=member.creator.username+' (Simulated member)';inspector.querySelector('select').append(option);
      }
    }catch(error){list.textContent=error.message;}
  }
  lab.addEventListener('toggle',()=>{if(lab.open)load();});
  const refresh=document.createElement('button');refresh.type='button';refresh.textContent='Refresh member quotas';refresh.onclick=()=>{loaded=false;list.replaceChildren();inspector.querySelector('select').replaceChildren();load();};lab.append(refresh);
  const stop=document.createElement('button');stop.type='button';stop.textContent='Stop isolated Lab services';stop.onclick=async()=>{await request('shutdown',{});list.textContent='Lab stopped. Your normal development account is unchanged.';};lab.append(stop);
  inspector.onsubmit=async event=>{event.preventDefault();const status=inspector.querySelector('pre');status.textContent='Verifying with the local service...';try{status.textContent=JSON.stringify(await request('inspect',{index:Number(inspector.querySelector('select').value),artwork:inspector.querySelector('input').value}),null,2);}catch(error){status.textContent=error.message;}};
  const back=document.createElement('p');back.textContent='Return to your normal development account by closing the isolated member window. Open multiple members for separate storage origins.';lab.append(back);
}
mount();
