"""Fail-closed package/log personal-identity audit with value-free diagnostics."""
import argparse,json,marshal,sys,types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery.privacy_patterns import categories
from git_identity import require_private_policy

def code_strings(code):
    if not isinstance(code,types.CodeType):return []
    result=[code.co_filename]
    for value in code.co_consts:
        if isinstance(value,str):result.append(value)
        elif isinstance(value,bytes):result.append(value)
        elif isinstance(value,types.CodeType):result.extend(code_strings(value))
    return result

def code_has_identity(code):
    values=code_strings(code)
    return bool(categories('\n'.join(v for v in values if isinstance(v,str))) or any(categories(v) for v in values if isinstance(v,bytes)))

def audit_package(package):
    from PyInstaller.archive.readers import CArchiveReader
    findings=set();files=0;modules=0
    for path in package.rglob('*'):
        if not path.is_file():continue
        files+=1
        if categories(path.relative_to(package).as_posix()) or categories(path.read_bytes()):findings.add('package-file')
    executable=package/(package.name+'.exe')
    archive=CArchiveReader(str(executable))
    if code_has_identity(marshal.loads(archive.extract('server'))):findings.add('frozen-server')
    pyz=archive.open_embedded_archive('PYZ.pyz')
    for name in pyz.toc:
        modules+=1
        if categories(name) or code_has_identity(pyz.extract(name)):findings.add('frozen-module')
    return {'files':files,'decodedModules':modules,'findingCategories':sorted(findings),'passed':not findings}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--package',type=Path)
    group.add_argument('--text',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        require_private_policy()
        result=audit_package(args.package) if args.package else {'passed':not bool(categories(args.text.read_bytes()))}
    except Exception:
        print('Personal-identity audit: INCOMPLETE; details withheld',file=sys.stderr);return 1
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Personal-identity audit: '+('PASS' if result['passed'] else 'REJECTED; protected identity detected; values withheld'))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
