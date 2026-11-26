#!/usr/bin/env python3
"""Seed CocoaPods' released-pod cache with exactly the two locked public ORT SDKs."""
import gzip,hashlib,json,os,shutil,subprocess,sys,tarfile
from pathlib import Path,PurePosixPath
PODS={'onnxruntime-c':'7f778680e96145956c0a31945f260321eed2611a','onnxruntime-objc':'83d28b87525bd971259a66e153ea32b5d023de19'}
HERE=Path(__file__).resolve().parent
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def checkname(v):
 path=PurePosixPath(v)
 if v.startswith('/') or '..' in path.parts or path.parts[0] not in set(PODS)|{'specs'}:raise ValueError('Unexpected archive path.')
def pack(pods,specs,output):
 with output.open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as comp,tarfile.open(mode='w',fileobj=comp,format=tarfile.USTAR_FORMAT) as arc:
  for name,sha in PODS.items():
   key='/'.join(hashlib.md5(name.encode()).hexdigest()[:3])
   spec=specs/key/name/'1.22.0'/(name+'.podspec.json')
   manifest=(pods/'Manifest.lock').read_text()
   if f'{name}: {sha}' not in manifest or json.loads(spec.read_text())['version']!='1.22.0':raise ValueError('Installed public pod must match the source lockfile.')
   if not (pods/name/'LICENSE').exists():raise ValueError('Expected public pod license.')
   source=[(spec,'specs/'+name+'.json')]+[(p,str(p.relative_to(pods))) for p in sorted((pods/name).rglob('*')) if not any(x.startswith('.') for x in p.relative_to(pods).parts)]
   for p,n in source:
    checkname(n)
    if p.is_symlink() and (os.path.isabs(os.readlink(p)) or not p.resolve().is_relative_to((pods/name).resolve())):raise ValueError('Nonportable symbolic link.')
    i=arc.gettarinfo(str(p),n);i.uid=i.gid=0;i.uname=i.gname='';i.mtime=0
    if p.is_file() and not p.is_symlink():
     with p.open('rb') as stream:arc.addfile(i,stream)
    else:arc.addfile(i)
 print('Verified dependency cache:',digest(output))
def restore(archive,cache):
 if digest(archive)!=(HERE/'ort-1.22.0.sha256').read_text().strip():raise ValueError('Dependency archive checksum mismatch.')
 stage=cache.parent/'extracted-public-pods';stage.mkdir()
 with tarfile.open(archive) as t:
  for member in t:
   checkname(member.name)
   if not(member.isfile() or member.isdir() or member.issym()):raise ValueError('Unsupported archive member')
   t.extract(member,stage,filter='data')
 root=cache/'Pods'
 root.mkdir(parents=True)
 (root/'VERSION').write_text(subprocess.check_output(['pod','--version'],text=True).strip())
 for name,sha in PODS.items():
  slug=Path('Release')/name/('1.22.0-'+sha[:5])
  path=root/slug;path.parent.mkdir(parents=True,exist_ok=True)
  shutil.move(stage/name,path)
  actual=root/'Specs'/(str(slug)+'.podspec.json');actual.parent.mkdir(parents=True,exist_ok=True)
  shutil.copyfile(stage/'specs'/(name+'.json'),actual)
 print('Restored the two locked public SDKs into the job-local CocoaPods cache.')
if __name__=='__main__':
 if sys.argv[1]=='pack':pack(*map(Path,sys.argv[2:]))
 elif sys.argv[1]=='restore':restore(*map(Path,sys.argv[2:]))
 else:raise SystemExit(2)
