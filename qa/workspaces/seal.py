"""Seal only verified synthetic application screenshots, never raw diagnostics."""
import hashlib,json,os,subprocess,sys,tarfile
from pathlib import Path
stage=Path(os.environ['QA_STAGE']).resolve()
assert os.environ['GITHUB_ACTIONS']=='true' and stage==Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa'
platform=sys.argv[1];assert platform in ('ios','android')
d=stage/'workspace-evidence'
partial=sys.argv[2:]==['--switch-only']
if partial:
    if not (d/'switch-verified').is_file(): sys.exit(0)
    assert (d/'switch-verified').read_text()==os.environ['QA_COMMIT']
    screens=['selected.png','workspaces.png']
else:
    assert (d/'verified').read_text()==os.environ['QA_COMMIT'] and (d/'isolation').read_text().startswith('Each of the three')
    screens=['selected.png','workspaces.png','created.png']
for f in screens:assert (d/f).read_bytes()[:8]==bytes.fromhex('89504e470d0a1a0a')
media=screens.copy()
if not partial:
    movie=d/'workspaces.mp4'
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type:format=duration','-of','json',str(movie)]))
    assert len(info['streams'])==1 and info['streams'][0]['codec_name']=='h264' and 2<=float(info['format']['duration'])<180
    media.append(movie.name)
manifest={'source':os.environ['QA_COMMIT'],'target':platform,'screens':{f:hashlib.sha256((d/f).read_bytes()).hexdigest() for f in media}}
(d/'receipt.json').write_text(json.dumps(manifest))
cert=Path(__file__).resolve().parents[1]/'recipient.pem'
assert b'PRIVATE KEY' not in cert.read_bytes()
with tarfile.open(stage/'review.private.tar','w') as tar:
    for f in media+['receipt.json']:tar.add(d/f,arcname=f,recursive=False)
out=stage.parent/('workspace-'+platform+('-switch-review.p7m' if partial else '-review.p7m'))
subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(stage/'review.private.tar'),'-out',str(out),str(cert)],check=True)
print('Sealed synthetic workspace evidence for its private visual review.')
