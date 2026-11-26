#!/usr/bin/env python3
"""Exercise a clean remote phone and seal each verified piece of evidence privately."""
import filecmp
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

os.umask(0o077)
assert os.environ.get('GITHUB_ACTIONS') == 'true'
platform=sys.argv[1]
assert platform in ('android','ios')
src=Path(__file__).resolve().parent
stage=Path(os.environ['QA_STAGE'])
logs=stage/'private-maestro'
logs.mkdir()
cert=src/'recipient.pem'
if b'PRIVATE KEY' in cert.read_bytes() or not cert.read_bytes().startswith(b'-----BEGIN CERTIFICATE-----'):
    raise SystemExit('Expected the public recipient certificate.')
driver='Android platform tools / uiautomator' if platform=='android' else 'Apple XCTest / simctl'
web_results={
    'sent-second-prompt': 'sent another prompt and verified the unique server reply in the official workspace',
    'read-native-history': 'opened the persisted native question and its complete server reply in the official workspace',
}
def run(command,**options):
    return subprocess.run(command,check=True,**options)

def seal(suffix):
    native_only=suffix in ('reply-review','native-review')
    dest=stage/('verified-'+suffix)
    dest.mkdir()
    screens=['chooser.png','pairing.png','transcript.png']
    if not native_only:screens.append('workspace.png')
    for name in screens:
        matches=list(logs.rglob(name))
        if len(matches)!=1 or matches[0].stat().st_size<1000:
            raise RuntimeError('Expected one non-empty verified screenshot for '+name)
        shutil.copyfile(matches[0],dest/(platform+'-'+name))
    reply_only=suffix=='reply-review'
    marker=stage/('reply-verified' if reply_only else 'native-verified')
    video=(stage/('reply-session.mp4' if reply_only else 'native-session.mp4')) if native_only else (logs/'garden-session.mp4')
    if not marker.is_file() or not video.is_file() or video.stat().st_size<1000:
        raise RuntimeError('The completed native scenario must provide its verified recording.')
    video_name=platform+'-garden-session.mp4'
    source_video=stage/'reply-session.mp4'
    if not reply_only and source_video.is_file() and filecmp.cmp(video,source_video,shallow=False):
        shutil.copyfile(stage/'verified-reply-review'/video_name,dest/video_name)
    else:
        run(['nice','-n','12','ffmpeg','-nostdin','-hide_banner','-loglevel','error','-threads','1','-i',str(video),'-map','0:v:0','-an','-map_metadata','-1','-filter_threads','1','-vf','scale=720:-2','-c:v','libx264','-threads','1','-crf','23','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',str(dest/video_name)])
    result='pending'
    if not native_only:
        key=(stage/'web-verification.txt').read_text().strip()
        result=web_results[key]
    (dest/'verification.json').write_text(json.dumps({
        'platform':platform,'conduit_source':os.environ['QA_COMMIT'],
        'harness':'0.2.2-rc.5','ui_automation':driver,'native_chat':'sent and received the complete streamed reply' if reply_only else 'sent, streamed and reopened after a cold launch',
        'official_workspace':result,'manual_privacy_review':'pending'
    },indent=2))
    archive=stage/(suffix+'.tar')
    with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
        for item in sorted(dest.iterdir()):
            info=tar.gettarinfo(str(item),item.name)
            info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
            with item.open('rb') as stream:tar.addfile(info,stream)
    sealed=Path(os.environ['RUNNER_TEMP'])/(platform+'-'+suffix+'.p7m')
    run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(archive),'-out',str(sealed),str(cert)])
    if sealed.stat().st_size<archive.stat().st_size:raise RuntimeError('Expected a sealed media bundle.')
    archive.unlink()
    print('Sealed verified '+suffix+' for private offline inspection.',flush=True)

command=src/('android-native-driver.py' if platform=='android' else 'xctest/ios-xctest-driver.py')
reply_proof=stage/'reply-verified'
proof=stage/'native-verified'
process=subprocess.Popen([sys.executable,str(command)])
deadline=time.monotonic()+(920 if platform=='ios' else 550)
reply_sealed=False
native_sealed=False
try:
    while True:
        status=process.poll()
        if reply_proof.is_file() and not reply_sealed:
            seal('reply-review')
            reply_sealed=True
        if proof.is_file() and not native_sealed:
            seal('native-review')
            native_sealed=True
        if status is not None:break
        if time.monotonic()>deadline:raise RuntimeError('Device scenario exceeded its deadline.')
        time.sleep(.4)
finally:
    if process.poll() is None:process.terminate();process.wait(timeout=15)
if process.returncode:
    raise SystemExit('The later scenario did not complete.' if native_sealed else 'Device scenario failed before verified native history.')
if not reply_sealed or not native_sealed:raise SystemExit('Missing required native checkpoint.')
seal('review')
