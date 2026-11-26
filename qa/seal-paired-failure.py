"""Seal a device screenshot and accessibility text only after a successful synthetic pairing."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

os.umask(0o077)
base = Path(os.environ['RUNNER_TEMP'])
stage = base/'garden-qa'
logs = stage/'private-maestro'
platform = sys.argv[1]
if not (stage/'token.private').is_file() or not list(logs.glob('**/paired-proof.png')):
    sys.exit(0)
device = os.environ.get('QA_DEVICE')
if not device:
    sys.exit(0)
try:
    if platform=='android':
        subprocess.run(['adb','-s',device,'shell','uiautomator','dump','/data/local/tmp/garden-diagnostic.xml'],capture_output=True,check=True,timeout=25)
        result=subprocess.run(['adb','-s',device,'shell','cat','/data/local/tmp/garden-diagnostic.xml'],capture_output=True,check=True,timeout=15)
    else:
        result = subprocess.run([os.environ['QA_MAESTRO'],'--device',device,'hierarchy'],
                                capture_output=True, timeout=40, check=True)
except (subprocess.SubprocessError,OSError):
    sys.exit(0)
hierarchy = result.stdout.decode(errors='replace')
token = (stage/'token.private').read_text().strip()
if not hierarchy or not token or token in hierarchy:
    sys.exit(0)
if re.search(r'server address|launch token|paste.{0,20}launch link|[?&]token=|dsh-auth-|\bBearer [\w-]+', hierarchy, re.I):
    sys.exit(0)
if not re.search(r'DeepSeek Harness|Open Harness workspace|New Chat|Seedling|arugula|basil|Field Notes|Message or run a task', hierarchy, re.I):
    sys.exit(0)
# Keep the hierarchy bounded and reject accidental email/credential text. Stock
# device coordinates, resource identifiers and the synthetic app UI survive.
if len(hierarchy)>250000 or re.search(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', hierarchy, re.I):
    sys.exit(0)
tree = stage/'paired-hierarchy.txt'
tree.write_text(hierarchy)
screen = stage/'paired-screen.png'
try:
    if platform == 'ios':
        subprocess.run(['xcrun','simctl','io',device,'screenshot',str(screen)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    elif platform == 'android':
        with screen.open('wb') as out:
            subprocess.run(['adb','-s',device,'exec-out','screencap','-p'],stdout=out,
                           check=True,stderr=subprocess.DEVNULL,timeout=25)
    else:
        sys.exit(0)
except (subprocess.SubprocessError,OSError):
    sys.exit(0)
if not screen.is_file() or not screen.read_bytes().startswith(b'\x89PNG'):
    sys.exit(0)
archive = stage/'paired-failure.tar'
with tarfile.open(archive,'w') as container:
    for item in (tree,screen):
        container.add(item, arcname=item.name)
subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER',
                '-in',str(archive),'-out',str(base/(platform+'-paired-failure.p7m')),
                str(Path(__file__).resolve().parent/'recipient.pem')],check=True)
archive.unlink()
