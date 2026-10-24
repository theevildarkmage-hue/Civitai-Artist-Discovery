"""Verify and start/reuse only this checkout's development instance. Never kills."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
HIDDEN=getattr(subprocess,'CREATE_NO_WINDOW',0)


def powershell(command):
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,
                          text=True,timeout=12,creationflags=HIDDEN)
    if result.returncode:raise RuntimeError('Windows process/port inspection failed; no instance was started.')
    return json.loads(result.stdout) if result.stdout.strip() else None


def owns_process(process,root=ROOT):
    if not process or Path(process.get('ExecutablePath') or '').name.lower() not in ('python.exe','pythonw.exe'):
        return False
    expected=str((Path(root)/'server.py').resolve()).casefold()
    arguments=re.findall(r'"[^"]*"|\S+',process.get('CommandLine') or '')
    return any(value.strip('"').casefold()==expected for value in arguments)


def read_json(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(NoRedirect()).open(url,timeout=5) as response:return json.load(response)


def development_environment(data):
    """Direct Python and PowerShell starts select the same preserved local ledger."""
    env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(data),'CAD_DEVELOPMENT':'1',
         'CAD_COMMUNITY_TESTING':'0','CAD_COMMUNITY_MODE':os.environ.get('CAD_COMMUNITY_MODE') or 'fixture'}
    sample=Path(data)/'sample.json'
    if env['CAD_COMMUNITY_MODE']=='fixture' and not env.get('CAD_COMMUNITY_SAMPLE') and sample.is_file():
        env['CAD_COMMUNITY_SAMPLE']=str(sample)
    return env


def owned_instance(data,port,*,listeners=None):
    if listeners is None:
        listeners=powershell(f"@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq {port} | Select-Object LocalAddress,OwningProcess) | ConvertTo-Json -Compress") or []
    if isinstance(listeners,dict):listeners=[listeners]
    if not listeners:return None
    record=data/'running-instance.json'
    if not record.exists():raise RuntimeError(f'Port {port} is occupied without this profile instance record. No process was stopped.')
    value=json.loads(record.read_text(encoding='utf-8'))
    pid=value.get('pid');url=f'http://127.0.0.1:{port}'
    if (type(pid) is not int or pid<=0 or value.get('url')!=url or
        any(row['OwningProcess']!=pid or row['LocalAddress']!='127.0.0.1' for row in listeners)):
        raise RuntimeError('The requested port belongs to another process/address or profile. No process was stopped.')
    process=powershell(f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress')
    if not owns_process(process):raise RuntimeError('The listener is not this Discovery 3.0 checkout. No process was stopped.')
    config=read_json(url+'/api/history/config')
    if config.get('version') not in ('3.0.0-dev','3.0.0-beta.1','3.0.0-beta.2'):raise RuntimeError('The listener does not report Discovery 3.0 development.')
    mode=None
    if config.get('developmentDiagnostics'):
        info=read_json(url+'/api/development/diagnostics')
        if (Path(info.get('sourceDirectory','')).resolve()!=ROOT or
                Path(info.get('dataDirectory','')).resolve()!=data.resolve() or info.get('processId')!=pid):
            raise RuntimeError('The running source/profile identity does not match this checkout.')
        mode=info.get('community',{}).get('mode')
    return {'pid':pid,'url':url,'mode':mode}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8907)
    parser.add_argument('--data-directory',type=Path)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--test-lab-connection',type=Path,help='Opt-in external connection file for the removable Community Test Lab')
    args=parser.parse_args()
    if not 0<args.port<65536 or args.port in (8906,33101,33201):raise RuntimeError('Use isolated port 8907 or another unoccupied development port.')
    for name in ('.git','server.py','discovery/community.py'):
        if not (ROOT/name).exists():raise RuntimeError(f'Wrong repository: missing {name}.')
    git_root=Path(subprocess.check_output(['git','-C',str(ROOT),'rev-parse','--show-toplevel'],text=True).strip()).resolve()
    if git_root!=ROOT:raise RuntimeError('The launcher is not in its expected Git checkout.')
    pin=ROOT/'.data-directory-3.0.json'
    chosen=args.data_directory or (json.loads(pin.read_text())['directory'] if pin.exists() else 'data-dev-3.0')
    data=(ROOT/chosen).resolve()
    if ROOT not in data.parents:raise RuntimeError('Development data must remain inside this checkout; credentials are never imported.')
    private=ROOT/'.venv-3.0';python=private/'Scripts/python.exe'
    if not python.exists():raise RuntimeError('Create .venv-3.0, then install requirements.txt using its Python. See docs/3.0-development.md.')
    info=json.loads(subprocess.check_output([str(python),'-c',
        'import sys,PIL,json;print(json.dumps({"prefix":sys.prefix,"base":sys._base_executable,"pillow":PIL.__version__}))'],text=True,creationflags=HIDDEN))
    if Path(info['prefix']).resolve()!=private or tuple(map(int,info['pillow'].split('.')[:3]))<(12,3,0):
        raise RuntimeError('The private environment requires Pillow 12.3.0+. Install the pinned requirements.')
    instance=owned_instance(data,args.port)
    if instance:
        if args.test_lab_connection:
            try:
                registration=read_json(instance['url']+'/__dev/community-test-lab/registration')
                if Path(registration['connectionFile']).resolve()!=args.test_lab_connection.resolve():raise ValueError()
            except Exception:
                raise RuntimeError('Close this verified development app through its Close app control, then relaunch with the requested Lab connection; no process was stopped.') from None
        wanted=os.environ.get('CAD_COMMUNITY_MODE','fixture')
        if instance.get('mode') and instance['mode']!=wanted:
            raise RuntimeError(f'This profile is already running in {instance["mode"]} mode. Close it through the app before selecting {wanted}; no process was stopped.')
        print(f"Reusing Discovery 3.0 at {instance['url']} (PID {instance['pid']}); profile: {data}")
        if not args.no_browser:webbrowser.open(instance['url'])
        return
    record=data/'running-instance.json'
    if record.exists():
        old=json.loads(record.read_text());pid=old.get('pid')
        if type(pid) is not int or pid<=0:raise RuntimeError('Invalid existing instance record; inspect it before starting another copy.')
        if powershell(f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | Select-Object ProcessId | ConvertTo-Json -Compress'):
            raise RuntimeError('This profile records another active process/port. Inspect its instance record; no competing copy was started.')
    env=development_environment(data)
    env['PYTHONPATH']=os.pathsep.join(filter(None,(str(private/'Lib/site-packages'),env.get('PYTHONPATH'))))
    logs=Path(tempfile.mkdtemp(prefix='cad3-development-'))
    with (logs/'startup.log').open('w',encoding='utf-8') as output:
        entry=[str(ROOT/'server.py')]
        if args.test_lab_connection:
            connection=args.test_lab_connection.resolve()
            if ROOT in connection.parents or not connection.is_file():raise RuntimeError('Use an external live Lab connection file')
            env['CAD_TEST_LAB_CONNECTION']=str(connection)
            entry=[str(ROOT/'dev/community-test-lab/launch.py'),str(ROOT/'server.py')]
        process=subprocess.Popen([info['base'],'-B',*entry,'--host','127.0.0.1',
            '--port',str(args.port),'--no-browser'],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,
            creationflags=HIDDEN,close_fds=True)
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError(f'Discovery exited at startup. Inspect {logs / "startup.log"}; check your profile and pinned requirements.')
        if record.exists():
            try:
                value=json.loads(record.read_text())
                if value.get('pid')==process.pid:
                    instance=owned_instance(data,args.port)
                    if instance and instance['pid']==process.pid:
                        print(f"Discovery 3.0 ready at {instance['url']} (PID {process.pid}); profile: {data}")
                        if not args.no_browser:webbrowser.open(instance['url'])
                        return
            except (OSError,ValueError):pass
        time.sleep(.25)
    raise RuntimeError(f'Startup was not confirmed. Inspect {logs}; no competing instance will be started on retry.')


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Discovery development startup failed: {error}')
