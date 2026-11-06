"""Prepare a nonsecret DPAPI canary; never exports a backup, key or credential.

The independent-account probe proves the current custody limitation, not portable
recovery. Actual portable recovery requires separately approved encryption/custody.
"""
import argparse,csv,hashlib,io,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from discovery.secure_backup import transform
PAYLOAD=b'Discovery 3.0 synthetic custody canary; no application data or secrets.'
def identity_hash():
    if os.name!='nt':raise RuntimeError('Windows-only canary; no fallback')
    result=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    rows=list(csv.reader(io.StringIO(result.stdout)))
    sid=next(row[-1].strip() for row in rows if row and row[-1].strip().startswith('S-1-'))
    return hashlib.sha256(sid.encode()).hexdigest()
def prepare(folder):
    folder=folder.resolve();folder.mkdir(parents=True,exist_ok=False)
    ciphertext=transform(PAYLOAD);assert transform(ciphertext,decrypt=True)==PAYLOAD
    modified=bytearray(ciphertext);modified[-1]^=1
    try:transform(bytes(modified),decrypt=True)
    except RuntimeError:pass
    else:raise RuntimeError('Canary tamper rejection failed')
    (folder/'canary.dpapi').write_bytes(ciphertext)
    manifest={'syntheticOnly':True,'sourceAccountSidSha256':identity_hash(),'ciphertextSha256':hashlib.sha256(ciphertext).hexdigest(),'payloadSha256':hashlib.sha256(PAYLOAD).hexdigest(),'sameAccountRoundTrip':True,'tamperDenied':True,'independentAccountExecuted':False,'portableRecoveryDemonstrated':False}
    (folder/'canary-manifest.json').write_text(json.dumps(manifest,indent=2));return manifest
def probe(folder):
    manifest=json.loads((folder/'canary-manifest.json').read_text());ciphertext=(folder/'canary.dpapi').read_bytes()
    if manifest.get('syntheticOnly') is not True or hashlib.sha256(ciphertext).hexdigest()!=manifest['ciphertextSha256']:raise RuntimeError('Synthetic canary manifest/integrity mismatch')
    if identity_hash()==manifest['sourceAccountSidSha256']:raise RuntimeError('Same account: independent-account probe refused')
    try:transform(ciphertext,decrypt=True)
    except RuntimeError:return {'syntheticOnly':True,'independentAccountExecuted':True,'originalAccountProtectionDenied':True,'portableRecoveryDemonstrated':False}
    raise RuntimeError('Unexpected cross-account decrypt; investigate account/key roaming before using this custody model')
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','probe']);p.add_argument('--directory',type=Path,required=True);p.add_argument('--receipt',type=Path);a=p.parse_args()
    result=prepare(a.directory) if a.action=='prepare' else probe(a.directory)
    if a.receipt:
        with a.receipt.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='sourceAccountSidSha256'}))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,RuntimeError) else 'Custody rehearsal stopped; no secret diagnostics',file=sys.stderr);raise SystemExit(1)
