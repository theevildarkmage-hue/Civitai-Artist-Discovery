"""Generate validated package-only configuration; never changes the source defaults."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery.build_config import validate,DEVELOPMENT_PROJECT

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--channel',choices=('beta','stable'),default='beta')
    p.add_argument('--version',required=True)
    p.add_argument('--environment',choices=('development','production'),required=True)
    p.add_argument('--project-ref');p.add_argument('--preview',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.environment=='production' and not a.project_ref:p.error('Production requires an explicit project reference')
    try:value=validate({'schemaVersion':1,'channel':a.channel,'version':a.version,'environment':a.environment,
        'projectRef':a.project_ref or DEVELOPMENT_PROJECT,'preview':a.preview})
    except ValueError as error:p.error(str(error))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps({'applicationId':'CivitaiDiscoveryBeta' if a.channel=='beta' else 'CivitaiArtistDiscovery',**value}))
if __name__=='__main__':main()
