"""Real Docker lifecycle checks; only disposable host bind mounts are used.
Run: python3 tests/container_docker.py [image]
Build the image first. Only the disposable published loopback endpoint is contacted; no provider APIs are used.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request

IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'discovery-container:local'

def docker(*args, check=True):
    return subprocess.run(['docker', *args], capture_output=True, text=True, check=check)

with tempfile.TemporaryDirectory(prefix='cad3-docker-') as temporary:
    root = Path(temporary)
    data = root / 'data'; data.mkdir()
    key = root / 'key'
    key.write_text(docker('run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE,
        '-c', 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())').stdout)
    key.chmod(0o644)  # Synthetic test key; readable under Docker Desktop ownership translation.
    name = 'cad3-test-' + uuid.uuid4().hex[:12]
    common = ['--network', 'none', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID',
              '--cap-add', 'SETGID', '--security-opt', 'no-new-privileges:true',
              '-v', f'{data}:/data', '-v', f'{key}:/run/secrets/credential_key:ro']
    try:
        networked = common.copy(); networked[1] = 'bridge'
        docker('run', '-d', '--name', name, *networked, '-p', '127.0.0.1::8765', '-e', 'WANTED_UID=12345', '-e', 'WANTED_GID=12346', IMAGE)
        probe = "import urllib.request,json; r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/history/config',headers={'Host':'localhost:8765'}),timeout=3); print(r.read().decode())"
        for _ in range(40):
            result = docker('exec', '--user', 'discovery', name, 'python', '-c', probe, check=False)
            if result.returncode == 0: break
            if docker('inspect', '-f', '{{.State.Running}}', name).stdout.strip() != 'true':
                raise AssertionError(docker('logs', name).stdout + docker('logs', name).stderr)
            time.sleep(.25)
        assert result.returncode == 0, result.stderr
        published = json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Ports}}', name).stdout)
        assert set(published) == {'8765/tcp'} and len(published['8765/tcp']) == 1
        binding = published['8765/tcp'][0]
        assert binding['HostIp'] == '127.0.0.1'
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}/api/history/config", headers={'Host':'localhost:8765'})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        config = json.loads(result.stdout)
        assert config['channel'] == 'stable' and config['environment'] == 'production'
        status = docker('exec', '--user', 'discovery', name, 'cat', '/proc/1/status').stdout
        assert 'Uid:\t12345\t12345\t12345\t12345' in status, status
        assert 'Gid:\t12346\t12346\t12346\t12346' in status, status
        assert 'CapEff:\t0000000000000000' in status
        assert 'CapPrm:\t0000000000000000' in status
        assert 'NoNewPrivs:\t1' in status
        assert docker('inspect', '-f', '{{json .Config.ExposedPorts}}', name).stdout.strip() == '{"8765/tcp":{}}'
        # Host and Origin guards, including the cross-site-only callback exception.
        http_checks = '''import http.client

def request(path, host='localhost:8765', origin=None, cross=False, post=False):
 c=http.client.HTTPConnection('127.0.0.1',8765); h={'Host':host}
 if origin:h['Origin']=origin
 if cross:h['Sec-Fetch-Site']='cross-site'
 if post:h['Content-Type']='application/json'
 c.request('POST' if post else 'GET',path,'{}' if post else None,h)
 r=c.getresponse();r.read();c.close();return r.status
assert request('/')==200
assert request('/',host='evil.example')==403
assert request('/',cross=True)==403
assert request('/oauth/callback?state=synthetic-secret&code=synthetic-code',cross=True)==400
assert request('/api/settings',origin='http://evil.example',post=True)==403
assert request('/api/app/restart',origin='http://localhost:8765',post=True)==409
'''
        docker('exec', '--user', 'discovery', name, 'python', '-c', http_checks)
        credentials = "from discovery import container_runtime as r; r.save({'access_token':'synthetic-persistent-token'}); assert r.load()['access_token']=='synthetic-persistent-token'"
        docker('exec', '--user', 'discovery', name, 'python', '-c', credentials)
        logs = docker('logs', name)
        assert 'synthetic-secret' not in logs.stdout + logs.stderr
        docker('stop', '-t', '20', name)
        assert docker('inspect', '-f', '{{.State.ExitCode}}', name).stdout.strip() == '0'
        assert not (data / 'running-instance.json').exists()
        docker('rm', name)
        preserved = {str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest() for p in data.rglob('*') if p.is_file()}
        # Recreate with the original bind mount and a different UID/GID: ownership repair is explicit.
        docker('run', '--rm', *common, '-e', 'WANTED_UID=12347', '-e', 'WANTED_GID=12348',
               '-e', 'FIX_DATA_OWNERSHIP=true', IMAGE, 'python', '-c',
               "from discovery import container_runtime as r; import os; assert os.getuid()==12347; assert r.load()['access_token']=='synthetic-persistent-token'")
        assert preserved == {str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest() for p in data.rglob('*') if p.is_file()}
        for value in ('0', 'invalid'):
            result = docker('run', '--rm', *common, '-e', f'WANTED_UID={value}', IMAGE, 'true', check=False)
            assert result.returncode != 0
        # Initialization defaults and failed access checks use separate disposable paths.
        empty = root / 'empty'; empty.mkdir()
        default_args = common.copy(); default_args[default_args.index(f'{data}:/data')] = f'{empty}:/data'
        docker('run', '--rm', *default_args, IMAGE, 'python', '-c', 'import os; assert os.getuid()==1000 and os.getgid()==1000')
        # A process launched through gosu cannot become root again.
        assert docker('run', '--rm', *common, '-e', 'WANTED_UID=12347', '-e', 'WANTED_GID=12348', IMAGE,
                      'python', '-c', 'import os; os.setuid(0)', check=False).returncode != 0
        # Refuse ownership repair across a nested mount.
        (data / 'nested').mkdir()
        result = docker('run', '--rm', *common, '--tmpfs', '/data/nested', '-e', 'FIX_DATA_OWNERSHIP=true', IMAGE, 'true', check=False)
        assert result.returncode != 0 and 'nested mounts' in result.stderr
        # Exercise real Linux permissions independently of Desktop bind translation.
        linux_args = common.copy(); position = linux_args.index(f'{data}:/data')
        linux_args[position-1:position+1] = ['--tmpfs', '/data']
        setup = "set -e; chmod 700 /data; touch /data/sentinel; chown 1000:1000 /data/sentinel /data; exec /usr/local/bin/discovery-init python -c 'import os; assert os.getuid()==1000; assert os.stat(\"/data\").st_mode & 0o777 == 0o700'"
        docker('run', '--rm', *linux_args, '--entrypoint', 'bash', IMAGE, '-c', setup)
        denied = "set -e; chmod 700 /data; touch /data/sentinel; exec /usr/local/bin/discovery-init true"
        result = docker('run', '--rm', *linux_args, '--entrypoint', 'bash', IMAGE, '-c', denied, check=False)
        assert result.returncode != 0 and 'cannot write /data' in result.stderr
        # Existing group 1 may be reused, but unrelated account UID 1 is rejected.
        assert docker('run', '--rm', *common, '-e', 'WANTED_UID=1', IMAGE, 'true', check=False).returncode != 0
        docker('run', '--rm', *common, '-e', 'WANTED_UID=12347', '-e', 'WANTED_GID=1',
               '-e', 'FIX_DATA_OWNERSHIP=true', IMAGE, 'python', '-c', 'import os; assert os.getgid()==1')
        # Invalid secret fails before serving, with nonzero process status.
        key.write_text('invalid')
        result = docker('run', '--rm', *common, '-e', 'WANTED_UID=12347', '-e', 'WANTED_GID=1', IMAGE, check=False)
        assert result.returncode != 0
        print('PASS: real gosu identity/capabilities, bind persistence, ownership repair, ID rejection, group reuse, HTTP guards, callback log redaction, SIGTERM, invalid key')
    finally:
        docker('rm', '-f', name, check=False)
