"""Fail-closed package/log personal-identity audit with value-free diagnostics."""
import argparse,io,json,marshal,sys,types,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
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

def inspect_container(data,name,depth=0,stats=None):
    """Decode reviewed containers; unreadable or unreviewed archives fail CLI."""
    if stats is None:stats={'zipMembers':0,'decodedBytes':0,'images':0}
    if Path(name).suffix.casefold() in {'.gz','.bz2','.xz','.7z','.rar','.tar','.tgz','.bundle','.pack','.msi','.cab','.iso','.dmg'}:
        raise ValueError('Unreviewed archive/container type')
    findings=set(categories(name))|set(categories(data))
    is_zip=zipfile.is_zipfile(io.BytesIO(data))
    if Path(name).suffix.casefold() in {'.zip','.whl','.egg','.jar','.docx','.xlsx','.pptx','.odt','.epub','.apk'} and not is_zip:
        raise ValueError('Unreadable known ZIP container')
    if is_zip:
        if depth>=4:raise ValueError('Container nesting exceeds the reviewed limit')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            findings.update(categories(archive.comment))
            for entry in archive.infolist():
                if entry.is_dir():continue
                stats['zipMembers']+=1;stats['decodedBytes']+=entry.file_size
                if entry.file_size>512*1024*1024 or stats['decodedBytes']>1024*1024*1024:raise ValueError('Container exceeds the reviewed limit')
                findings.update(categories(entry.comment));findings.update(categories(entry.extra))
                findings.update(inspect_container(archive.read(entry),entry.filename,depth+1,stats))
    if Path(name).suffix.casefold() in {'.png','.jpg','.jpeg','.webp','.gif','.ico','.tif','.tiff'}:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            stats['images']+=1
            def metadata(value):
                if isinstance(value,(str,bytes)):findings.update(categories(value))
                elif isinstance(value,dict):
                    for key,item in value.items():metadata(key);metadata(item)
                elif isinstance(value,(list,tuple)):
                    for item in value:metadata(item)
            metadata(dict(image.info));metadata(dict(image.getexif()))
    return findings

def audit_package(package):
    from PyInstaller.archive.readers import CArchiveReader
    findings=set();files=0;modules=0;stats={'zipMembers':0,'decodedBytes':0,'images':0}
    for path in package.rglob('*'):
        if not path.is_file():continue
        files+=1
        if inspect_container(path.read_bytes(),path.relative_to(package).as_posix(),stats=stats):findings.add('package-file')
    executable=package/(package.name+'.exe')
    archive=CArchiveReader(str(executable))
    if code_has_identity(marshal.loads(archive.extract('server'))):findings.add('frozen-server')
    pyz=archive.open_embedded_archive('PYZ.pyz')
    for name in pyz.toc:
        modules+=1
        if categories(name) or code_has_identity(pyz.extract(name)):findings.add('frozen-module')
    return {'files':files,'decodedModules':modules,'decodedContainers':stats,'findingCategories':sorted(findings),'passed':not findings}

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
