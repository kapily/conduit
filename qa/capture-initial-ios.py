"""Seal the clean hosted iPhone's first screen before the fixture can issue a credential."""
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import uuid

os.umask(0o077)
stage=Path(os.environ['QA_STAGE'])
assert os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('GITHUB_ACTIONS')=='true'
assert stage.resolve()==(Path(os.environ['RUNNER_TEMP'])/'garden-qa').resolve()
assert not (stage/'token.private').exists()
device=str(uuid.UUID(os.environ['QA_DEVICE'])).upper()
assert device==(stage/'ios-device').read_text().strip().upper()
bundle='app.cogwheel.conduit.debug'
out=stage/'first-launch'
out.mkdir()
def command(argv,wait):
    try:
        r=subprocess.run(argv,capture_output=True,timeout=wait)
        return r.stdout,r.stderr,r.returncode
    except subprocess.TimeoutExpired:
        return b'',b'',124
def sim(args,wait=18):
    return command(['xcrun','simctl',*args],wait)
inventory=json.loads(sim(['list','devices','--json'])[0])
assert any(d.get('udid','').upper()==device and d.get('state')=='Booted' and d.get('name')=='Garden QA'
           for group in inventory['devices'].values() for d in group)
apps,_,apps_status=sim(['listapps',device])
installed=bundle.encode() in apps
stdout,_,launch=sim(['launch',device,bundle],120)
pid=re.search(rb':\s*(\d+)',stdout)
time.sleep(3)
screen=out/'first-screen.png'
_,_,screenshot_status=sim(['io',device,'screenshot',str(screen)],35)
report={'known_debug_app_installed':installed,'listapps_status':apps_status,'launch_status':launch,
        'launch_returned_pid':bool(pid),'screenshot_status':screenshot_status}
assert not (stage/'token.private').exists()
axe=str(stage/'axe-2.2.0/axe')
tree,_,ax_status=command([axe,'describe-ui','--minimal-keys','--udid',device],120)
report['accessibility_status']=ax_status
tree_path=out/'empty-accessibility.txt'
if ax_status==0 and 0<len(tree)<=400000 and not re.search(rb'[?&]token=|dsh-auth-|\bBearer |BEGIN [A-Z ]*PRIVATE KEY',tree,re.I):
    tree_path.write_bytes(tree)
log,_,_=sim(['spawn',device,'log','show','--last','2m','--style','compact','--predicate','process == "Runner" OR process == "Conduit"'],25)
allowed=['Flutter','Dart','JIT','unhandled exception','library not loaded','launch failed','failed to start','out of memory','segmentation fault','symbol not found']
report['startup_log_categories']=[t for t in allowed if t.casefold() in log.decode(errors='ignore').casefold()]
report_path=out/'known-launch-status.json'
report_path.write_text(json.dumps(report,indent=2))
print('Initial iOS status:',json.dumps(report),flush=True)
assert not (stage/'token.private').exists()
archive=out/'pre-pair.tar'
with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
    for item in (report_path,screen,tree_path):
        if item.is_file():tar.add(item,arcname=item.name)
cert=Path(__file__).resolve().parent/'recipient.pem'
subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(archive),
                '-out',str(Path(os.environ['RUNNER_TEMP'])/'ios-first-screen.p7m'),str(cert)],check=True)
archive.unlink()
