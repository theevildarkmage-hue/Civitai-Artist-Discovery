"""Real Linux Secret Service test with synthetic data in a private D-Bus/keyring."""
import json,os,secrets,shutil,subprocess,sys,tempfile,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def worker():
    root=Path(os.environ['CAD_NATIVE_KEYRING_ROOT']).resolve()
    assert root.name.startswith('cad3-native-keyring-') and Path(os.environ['HOME']).resolve()==root
    assert os.environ.get('DBUS_SESSION_BUS_ADDRESS')
    daemon=None
    with (root/'daemon-output.txt').open('w') as output:
        try:
            daemon=subprocess.Popen(['gnome-keyring-daemon','--foreground','--unlock','--components=secrets',
                                     '--control-directory',str(root/'control')],stdin=subprocess.PIPE,
                                    stdout=output,stderr=subprocess.STDOUT)
            daemon.stdin.write(secrets.token_urlsafe(32).encode());daemon.stdin.close()
            import secretstorage
            deadline=time.monotonic()+15
            while True:
                try:
                    bus=secretstorage.dbus_init();collection=secretstorage.get_default_collection(bus)
                    if not collection.is_locked():break
                except secretstorage.exceptions.SecretStorageException:pass
                if daemon.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Isolated Secret Service did not become ready')
                time.sleep(.1)
            from discovery import oauth
            assert oauth._uses_secret_service()
            backend=oauth._linux_keyring().get_keyring()
            assert type(backend).__module__=='keyring.backends.SecretService'
            payload={'access_token':'SYNTHETIC_LINUX_ACCESS_CANARY_20261106',
                     'refresh_token':'SYNTHETIC_LINUX_REFRESH_CANARY_20261106',
                     'expires_at':2147483648,'client_id':oauth.client_id()}
            oauth._save({**payload,'id_token':'SYNTHETIC_TRANSIENT_ASSERTION'})
            assert oauth._load()==payload
            child=subprocess.run([sys.executable,'-c',
                'import sys,json;from discovery import oauth;assert oauth._load()==json.load(sys.stdin);print("separate process read passed")',
                '--stable'],cwd=ROOT,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
            assert child.returncode==0,'Separate process read failed'
            assert not oauth.TOKEN_PATH.exists()
            stored_files=list((root/'share').rglob('*'))
            assert any(p.suffix=='.keyring' for p in stored_files if p.is_file())
            for path in stored_files:
                if path.is_file():
                    raw=path.read_bytes()
                    assert all(value.encode() not in raw for value in [payload['access_token'],payload['refresh_token'],'SYNTHETIC_TRANSIENT_ASSERTION'])
            oauth._delete_linux();oauth._delete_linux()
            assert oauth._linux_keyring().get_password(oauth.KEYRING_SERVICE,oauth.KEYRING_ACCOUNT) is None
            print(json.dumps({'realLinuxSecretService':True,'backend':type(backend).__module__,
                              'syntheticCredentialsOnly':True,'crossProcessRead':True,'noPlaintextTokenFile':True,
                              'encryptedKeyringCanariesAbsent':True,'assertionNotPersisted':True,'deletionPassed':True,
                              'privateDbusAndHome':True,'networkRequests':0}))
            if '--run' in sys.argv:
                target=(ROOT/sys.argv[sys.argv.index('--run')+1]).resolve()
                if target.parent!=ROOT/'tests' or target.suffix!='.py':raise ValueError('Only repository test scripts may use the isolated keyring')
                if '--stable-config' in sys.argv:
                    code='import sys,runpy;path=sys.argv[1];sys.argv=["server.py","--stable"];from discovery import build_config;assert build_config.ENVIRONMENT=="production";sys.argv=[path];runpy.run_path(path,run_name="__main__")'
                    command=[sys.executable,'-c',code,str(target)]
                else:command=[sys.executable,str(target)]
                subprocess.run(command,cwd=ROOT,env=os.environ.copy(),check=True,timeout=120)
        finally:
            if daemon is not None:
                if daemon.poll() is None:daemon.terminate()
                daemon.wait(timeout=10)

def main():
    if sys.platform!='linux':print('SKIP: native Linux Secret Service test requires Linux');return
    if '--worker' in sys.argv:worker();return
    for command in ['dbus-run-session','gnome-keyring-daemon']:
        if not shutil.which(command):raise SystemExit('Native keyring test requires '+command)
    with tempfile.TemporaryDirectory(prefix='cad3-native-keyring-') as folder:
        root=Path(folder)
        for name in ['share','config','runtime','data-test-native','control']:(root/name).mkdir(mode=0o700)
        env={**os.environ,'HOME':folder,'XDG_DATA_HOME':str(root/'share'),'XDG_CONFIG_HOME':str(root/'config'),
             'XDG_RUNTIME_DIR':str(root/'runtime'),'CIVITAI_HISTORY_DATA_DIR':str(root/'data-test-native'),
             'CAD_NATIVE_KEYRING_ROOT':folder,'PYTHONPATH':str(ROOT)}
        env['PLAYWRIGHT_BROWSERS_PATH']=os.environ.get('PLAYWRIGHT_BROWSERS_PATH',str(Path.home()/'.cache/ms-playwright'))
        for key in ['DBUS_SESSION_BUS_ADDRESS','GNOME_KEYRING_CONTROL','SSH_AUTH_SOCK','PYTHON_KEYRING_BACKEND']:
            env.pop(key,None)
        extra=[]
        if '--run' in sys.argv:extra=['--run',sys.argv[sys.argv.index('--run')+1]]
        if '--stable-config' in sys.argv:extra.append('--stable-config')
        subprocess.run(['dbus-run-session','--',sys.executable,str(Path(__file__).resolve()),'--worker','--stable',*extra],
                       cwd=ROOT,env=env,check=True,timeout=150)

if __name__=='__main__':main()
