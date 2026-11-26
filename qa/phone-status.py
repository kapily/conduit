"""Print only fixed status and known first-run labels from the synthetic Android."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

if sys.argv[1] != 'android':sys.exit(0)
stage = Path(os.environ['RUNNER_TEMP'])/'garden-qa'
device = os.environ.get('QA_DEVICE', 'emulator-5554')
app = 'app.cogwheel.conduit.debug'
def adb(*args, timeout=8):
    try:
        p=subprocess.run(['adb','-s',device,*args],capture_output=True,timeout=timeout)
        return p.stdout.decode(errors='replace') if p.returncode==0 else ''
    except (subprocess.SubprocessError,OSError):
        return ''
def memory():
    info=Path('/proc/meminfo').read_text()
    found=re.search(r'MemAvailable:\s*(\d+)',info)
    events=Path('/sys/fs/cgroup/memory.events')
    killed=re.search(r'^oom_kill\s+(\d+)',events.read_text(),re.M) if events.exists() else None
    print('Available MiB:', int(found[1])//1024 if found else 'unknown',
          'cgroup OOM kills:',killed[1] if killed else 'unknown')
memory()
p=stage/'emulator.pid'
alive=p.exists() and Path('/proc',p.read_text().strip()).exists()
state=adb('get-state').strip()
code=stage/'emulator.exit'
exit_code=code.read_text().strip() if code.exists() else ''
print('Emulator process present:',alive,'ADB device present:',state=='device',
      'exit code:',int(exit_code) if exit_code.isdigit() else 'unavailable')
if sys.argv[2]=='initial' and state=='device':
    adb('shell','monkey','-p',app,'-c','android.intent.category.LAUNCHER','1')
    time.sleep(6)
if state=='device':
    print('Conduit process present:',bool(adb('shell','pidof',app).strip()))
    view='/data/local/tmp/garden-view.xml'
    adb('shell','uiautomator','dump',view,timeout=20)
    xml=adb('shell','cat',view)
    if xml:
        try:
            tree=ET.fromstring(xml)
            package_nodes=[n for n in tree.iter('node') if n.get('package')==app]
            words=' '.join(n.get(k,'') for n in package_nodes for k in ('text','content-desc')).casefold()
            known=['Conduit','Open WebUI','LibreChat','DeepSeek Harness','Hermes','Server Address','Launch token','Connect','New Chat','No conversations yet','Message...','Send','Chats','Open Harness workspace']
            print('Conduit visible:',bool(package_nodes),'known labeled controls:',json.dumps([v for v in known if v.casefold() in words]))
        except ET.ParseError:print('Hierarchy was incomplete.')
    if sys.argv[2]=='initial':
        crash=adb('logcat','-b','crash','-d')
        print('Conduit pre-pair crash:',app in crash)
else:
    log=stage/'emulator.log'
    if log.is_file():
        known=['segmentation fault','killed','fatal','panic','out of memory','disk space','shutting down','received signal','vulkan','opengl','swiftshader']
        notes=log.read_text(errors='replace').casefold()
        print('Runner emulator diagnostic categories:',json.dumps([v for v in known if v in notes]))
