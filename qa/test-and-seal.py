#!/usr/bin/env python3
import json,os,shutil,subprocess,sys,tarfile
from pathlib import Path

os.umask(0o077);src=Path(__file__).resolve().parent;stage=Path(os.environ['QA_STAGE'])
assert os.environ.get('GITHUB_ACTIONS')=='true'
logs=stage/'private-maestro';logs.mkdir()
token=(stage/'token.private').read_text().strip()
flow=stage/'flow.private.yaml'
flow.write_text((src/'flow.yaml').read_text().replace('__LOCAL_LAUNCH_TOKEN__',token))
cert=src/'recipient.pem'
certificate=cert.read_bytes()
if b'PRIVATE KEY' in certificate or not certificate.startswith(b'-----BEGIN CERTIFICATE-----'):
 raise SystemExit('Expected a public X.509 recipient certificate.')
def run(cmd,**kw):return subprocess.run(cmd,check=True,**kw)
with (stage/'private-maestro.log').open('w') as out:
 p=subprocess.run([os.environ['QA_MAESTRO'],'--device',os.environ['QA_DEVICE'],'test','--test-output-dir',str(logs),str(flow)],stdout=out,stderr=out,timeout=590)
if p.returncode:
 # Reveal only the names of passed assertions. No trees, screenshots, input values, or error traces.
 clean=(stage/'private-maestro.log').read_text(errors='replace')
 for s in ['New Chat','Label each pot','Open Harness workspace','Message or run a task','stopRecording']:
  print('Reached expected control',repr(s),s.casefold() in clean.casefold())
 raise SystemExit('Device scenario failed. Raw Maestro diagnostics are excluded from artifacts.')
dest=stage/'verified';dest.mkdir()
plat=sys.argv[1]
# Explicit allowlist. Maestro's implicit failure screenshots, control traces and session logs are private.
for fname in ['chooser.png','pairing.png','transcript.png','workspace.png','garden-session.mp4']:
 matches=list(logs.rglob(fname))
 if len(matches)!=1 or matches[0].stat().st_size<1000:raise SystemExit('No unique verified output for '+fname)
 name=plat+'-'+fname
 if fname.endswith('.mp4'):
  run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(matches[0]),'-map','0:v:0','-an','-map_metadata','-1','-vf','scale=720:-2','-c:v','libx264','-crf','23','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',str(dest/name)])
 else:shutil.copyfile(matches[0],dest/name)
(dest/'verification.json').write_text(json.dumps({'platform':plat,'conduit_source':os.environ['QA_COMMIT'],'harness':'0.2.2-rc.5','ui_automation':'2.12.1','native_chat':'sent and reopened after a cold launch','official_workspace':'sent a separate prompt, verified its unique streamed reply, and returned to native chats','manual_privacy_review':'pending'},indent=2))
archive=stage/'unpublished.tar'
with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as t:
 for f in sorted(dest.iterdir()):
  info=t.gettarinfo(str(f),f.name);info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
  with f.open('rb') as stream:t.addfile(info,stream)
sealed=Path(os.environ['RUNNER_TEMP'])/(plat+'-review.p7m')
run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(archive),'-out',str(sealed),str(cert)])
if sealed.stat().st_size<archive.stat().st_size:raise RuntimeError('Empty envelope.')
flow.unlink();archive.unlink()
print('Device checks passed. The allowlisted media is encrypted for offline review.')
