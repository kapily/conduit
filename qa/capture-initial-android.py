"""Capture an explicitly clean Android launch before Harness or its grant exists."""
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET

os.umask(0o077)
stage=Path(os.environ['QA_STAGE'])
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
assert stage.resolve()==(Path(os.environ['RUNNER_TEMP'])/'garden-qa').resolve()
assert not (stage/'token.private').exists()
device=os.environ['QA_DEVICE'];assert device=='emulator-5554'
package='app.cogwheel.conduit.debug'
def adb(*args,timeout=12):
    try:
        proc=subprocess.run(['adb','-s',device,*args],capture_output=True,timeout=timeout)
        return proc.stdout if proc.returncode==0 else b''
    except subprocess.TimeoutExpired:return b''
def shell(*args,**kw):return adb('shell',*args,**kw).decode(errors='replace')
assert adb('emu','avd','name').splitlines()[0]==b'GardenQA'
resolved=shell('cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER',package)
component=next((l.strip() for l in resolved.splitlines() if re.fullmatch(re.escape(package)+r'/[A-Za-z0-9_.$]+',l.strip())),None)
assert component
shell('input','keyevent','KEYCODE_WAKEUP');shell('wm','dismiss-keyguard')
launch=shell('am','start','-W','-n',component,timeout=55)
dir=stage/'initial-android';dir.mkdir()
path='/data/local/tmp/garden-clean-first.xml'
probe=''
for _ in range(4):
    shell('uiautomator','dump',path,timeout=20)
    probe=shell('cat',path)
    if package in probe:break
    time.sleep(2)
assert not (stage/'token.private').exists()
known=['Conduit','Open WebUI','Hermes','DeepSeek Harness','Server Address','Launch token','Connect','Allow','Wait','Close app','Continue','Get started','Set up your Pixel','Got it']
visible=[]
if probe and '<hierarchy' in probe and len(probe)<500000:
    try:tree=ET.fromstring(probe)
    except ET.ParseError:tree=None
    if tree is not None:
        visible=sorted({n.get('package','') for n in tree.iter('node') if n.get('package')})
        if not re.search(r'[?&]token=|dsh-auth-|\bBearer |BEGIN .*PRIVATE KEY',probe,re.I):
            (dir/'first-hierarchy.xml').write_text(probe)
allowed_packages={package,'com.android.systemui','com.google.android.apps.nexuslauncher','com.google.android.setupwizard','com.google.android.permissioncontroller','com.android.permissioncontroller','android','com.android.launcher3','com.android.settings','com.google.android.gms'}
report={'launcher_resolved':True,'launch_reported_ok':'Status: ok' in launch,'app_has_pid':bool(shell('pidof',package).strip()),
        'visible_known_packages':[p for p in visible if p in allowed_packages],
        'other_package_count':sum(p not in allowed_packages for p in visible),
        'known_labels':[word for word in known if word.casefold() in probe.casefold()]}
screen=adb('exec-out','screencap','-p')
if screen.startswith(b'\x89PNG'):(dir/'first-screen.png').write_bytes(screen)
(dir/'known-launch-status.json').write_text(json.dumps(report,indent=2))
print('Fresh Android launch:',json.dumps(report),flush=True)
assert not (stage/'token.private').exists()
archive=dir/'first.tar'
with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
    for name in ['first-screen.png','first-hierarchy.xml','known-launch-status.json']:
        item=dir/name
        if item.is_file():tar.add(item,arcname=name)
subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(archive),'-out',str(Path(os.environ['RUNNER_TEMP'])/'android-first-screen.p7m'),str(Path(__file__).resolve().parent/'recipient.pem')],check=True)
archive.unlink()
