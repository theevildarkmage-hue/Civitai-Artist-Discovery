import {api} from './api.js';
const panel=document.createElement('details');panel.id='registryDisclosure';
const title=document.createElement('summary');title.textContent='Beta privacy and authenticated activity';
const text=document.createElement('p');text.textContent='Optional authenticated activity registration stores your verified public Civitai account ID and username, first use and activity times, reported app version and platform. It does not store OAuth tokens, device identifiers or browsing history. Updates occur at most every 12 hours. Only authorized operators can inspect usage statistics or handle correction and deletion requests. Community invitations are separate. Turning this off stops future collection; contact the beta owner to request deletion of existing records. Hosting providers may retain separate access logs containing network metadata.';
const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.id='registryConsent';
label.append(check,document.createTextNode(' Allow authenticated activity registration under this disclosure'));
const state=document.createElement('p');state.setAttribute('role','status');
panel.append(title,text,label,state);document.querySelector('header').after(panel);
async function refresh(){try{const value=await api('/api/registry/consent');check.checked=value.consented;check.disabled=!value.collectionEnabled;state.textContent=value.collectionEnabled?'You can withdraw this choice at any time.':'Collection is disabled pending privacy approval and backend acceptance.';}catch{check.disabled=true;state.textContent='Privacy settings are temporarily unavailable; collection remains off.';}}
check.onchange=async()=>{check.disabled=true;try{await api('/api/registry/consent',{method:'POST',body:JSON.stringify({consented:check.checked})});}finally{await refresh();}};
refresh();
