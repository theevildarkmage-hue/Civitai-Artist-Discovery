// Read-only developer tools are advertised only by an explicit development server.
import { api } from './api.js';

export async function mountDevelopmentDiagnostics() {
  const config = await api('/api/history/config').catch(() => ({}));
  if (!config.developmentDiagnostics) return;
  const panel = document.createElement('details');
  panel.id = 'developmentDiagnostics';
  panel.className = 'community-diagnostics';
  panel.innerHTML = '<summary>Application diagnostics</summary><p>Read-only cached state. Run Diagnostics adds two short, unauthenticated API connectivity probes. No synchronization or recommendations are started.</p><button type="button" id="runDevelopmentDiagnostics">Run Diagnostics</button><p id="developmentDiagnosticsStatus" role="status"></p><pre id="developmentDiagnosticsOutput" class="development-diagnostics-output"></pre>';
  document.getElementById('discovery').append(panel);
  const shortcut = document.createElement('button');
  shortcut.type = 'button'; shortcut.id = 'openDevelopmentDiagnostics';
  shortcut.textContent = 'Development diagnostics';
  document.getElementById('preferencesMenu').append(shortcut);
  shortcut.onclick = () => {
    document.getElementById('preferencesMenu').classList.add('hidden');
    document.getElementById('tabDiscovery').click(); panel.open = true;
    panel.scrollIntoView({block:'start'});
  };
  const output=panel.querySelector('pre'), status=panel.querySelector('[role="status"]');
  const button=panel.querySelector('button');
  async function load(probe=false) {
    button.disabled=true;status.textContent=probe?'Checking cached state and API connectivity…':'Reading cached state…';
    try {
      const value=await api(`/api/development/diagnostics${probe?'?probe=1':''}`);
      output.textContent=JSON.stringify(value,null,2);
      status.textContent='Diagnostics complete. Cached data and recommendations were not changed.';
    } catch(error) { status.textContent=error.message; }
    finally { button.disabled=false; }
  }
  panel.addEventListener('toggle',()=>{ if(panel.open && !output.textContent) load(); });
  button.onclick=()=>load(true);
}
