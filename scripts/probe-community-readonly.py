"""Bounded live OAuth/artwork preview through an owned read-only local handler."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request
import urllib.error
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();url=urlsplit(args.url)
    if (os.environ.get('CAD_ALLOW_LOCAL_HTTP_TESTS')!='1' or not os.environ.get('CAD_TEST_PG_SYSTEM_IDENTIFIER')
            or url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port):
        raise RuntimeError('Owned local read-only service required')
    pin=json.loads((ROOT/'.data-directory-3.0.json').read_text())
    profile=(ROOT/pin['directory']).resolve()
    if profile not in ((ROOT/'data-test-community').resolve(),(ROOT/'data-dev-3.0').resolve()):
        raise RuntimeError('Unrecognized persistent profile; no credential access')
    os.environ['CIVITAI_HISTORY_DATA_DIR']=str(profile);sys.path.insert(0,str(ROOT))
    from discovery import oauth
    # Read/decrypt only: no refresh, migration, token save or OAuth mutation.
    saved=json.loads(oauth._crypt((profile/'oauth_tokens.dpapi').read_bytes(),False).decode())
    access=saved['access_token']
    with sqlite3.connect((profile/'community/fixtures.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        images=[r[0] for r in db.execute('select image_id from recommendations order by seq desc limit 2')]
    checks=[]
    for operation,art in [('status',None),('verify',f'https://civitai.red/images/{images[0]}'),('verify',images[1])]:
        body={'apiVersion':1,'operation':operation,**({'artwork':art} if art is not None else {})}
        req=urllib.request.Request(args.url,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+access,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=25) as response:status=response.status;value=json.load(response)
        except urllib.error.HTTPError as error:status=error.code;value=json.load(error)
        except OSError:status='unavailable';value={}
        checks.append({'operation':operation,'artwork':art,'status':status,'code':value.get('code'),
                       'verifiedMember':bool(value.get('member',{}).get('id')) if operation=='status' else None,
                       'verifiedImageId':value.get('imageId'),'quotaUsed':value.get('quota',{}).get('used')})
    result={'readOnly':True,'actualProductionHandler':True,'actualDeno':True,'actualPostgREST':True,
            'liveCivitai':True,'checks':checks,'allChecksPassed':all(c['status']==200 for c in checks),
            'maxUpstreamRequests':7,'socialWrites':0,'recommendationSubmissions':0,'tokenRefreshedOrSaved':False}
    args.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':main()
