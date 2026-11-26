"""Verify same simulator, fixture, native test receipts and app data container across an in-place install."""
import hashlib
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import uuid
from urllib.request import ProxyHandler,Request,build_opener
from urllib.error import HTTPError

APP='app.cogwheel.conduit.debug'

def check(ok,msg):
    if not ok:raise RuntimeError(msg)

def sim(*args):
    proc=subprocess.run(['xcrun','simctl',*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=70)
    if proc.returncode:raise RuntimeError('Simulator command did not finish.')
    return proc.stdout.decode().strip()

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def handoff(stage,dev):
    out=stage/'integration-verified'
    check(all((out/n).is_file() for n in ('reply-verified','history-verified','ios-native-garden.mp4','ios-history.png','ios-transcript.png')),'The complete reply and reopened native history receipts are required.')
    check((out/'reply-verified').read_text().strip()=='Native user message and complete server response verified.' and (out/'history-verified').read_text().strip()=='The single freshly created server session reopened in the native interface.','Unexpected native receipts.')
    check(all((out/n).stat().st_size>1000 for n in ('ios-native-garden.mp4','ios-history.png','ios-transcript.png')),'Incomplete widget evidence.')
    check((stage/'ios-device').read_text().strip().upper()==str(uuid.UUID(dev)).upper(),'Wrong hosted simulator.')
    check((stage/'widget-data-container.private').is_file(),'Missing container checkpoint.')
    keep=Path((stage/'widget-data-container.private').read_text()).resolve()
    check(keep.is_dir() and Path(sim('get_app_container',dev,APP,'data')).resolve()==keep,'The instrumented app store was already removed.')
    original=stage/'app-build/Conduit.app'
    check(plistlib.loads((original/'Info.plist').read_bytes())['CFBundleIdentifier']==APP,'Wrong original bundle.')
    current=Path(sim('get_app_container',dev,APP,'app'))
    relative_paths=('Frameworks/App.framework/flutter_assets/kernel_blob.bin','Frameworks/App.framework/App','Runner')
    diff=None
    for rel in relative_paths:
        if (original/rel).is_file() and (current/rel).is_file() and digest(original/rel)!=digest(current/rel):
            diff=rel;break
    check(diff is not None,'Could not distinguish original and instrumented application code.')
    before=digest(original/diff)
    # Only installation is allowed here. Uninstallation would destroy the app data.
    sim('install',dev,str(original))
    check(Path(sim('get_app_container',dev,APP,'data')).resolve()==keep,'App data container changed across the replacement.')
    installed=Path(sim('get_app_container',dev,APP,'app'))
    check(digest(installed/diff)==before,'Installed application does not match original cached build.')
    # Keep the original fixture and actual production sign-in. No secure storage reads.
    server=int((stage/'server.pid').read_text());model=int((stage/'model.pid').read_text())
    os.kill(server,0);os.kill(model,0)
    (stage/'original-with-retained-data').write_text(os.environ['QA_COMMIT'])
    print('Full native widget receipts verified. The exact original app is installed over the same retained data container and the original fixture is alive.',flush=True)

if __name__=='__main__':
    try:
        os.umask(0o077)
        stage=Path(os.environ['QA_STAGE']).resolve()
        check(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='macOS' and stage==Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa','Expected the private hosted iOS QA stage.')
        handoff(stage,os.environ['QA_DEVICE'])
    except Exception as e:
        print(str(e) if isinstance(e,RuntimeError) else 'Original app handoff failed ('+type(e).__name__+').',file=sys.stderr)
        sys.exit(1)
