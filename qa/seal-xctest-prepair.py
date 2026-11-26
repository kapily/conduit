"""Seal a screenshot from a verified blank XCTest flow when its pairing step fails."""
import os
from pathlib import Path
import re
import subprocess
import tarfile

os.umask(0o077)
base = Path(os.environ['RUNNER_TEMP'])
stage = base/'garden-qa'
safe = stage/'xctest-prepair-safe'
screen = stage/'xctest-prepair.png'
text = stage/'xctest-prepair.txt'
token = stage/'token.private'
if not all(p.is_file() for p in (safe,screen,text,token)):
    raise SystemExit(0)
if (stage/'private-maestro/paired-proof.png').exists():
    raise SystemExit(0)
value = token.read_text().strip()
status = text.read_text()
if not value or value in status or not 0<len(status)<24000 or re.search(
    r'http[s]?://|[?&]token=|dsh-auth-|[\w.+-]+@[\w.-]+\.[a-z]{2,}|/(?:Users|Volumes|home|private)/',status,re.I
):
    raise SystemExit(0)
if not 1000<screen.stat().st_size<5_000_000 or screen.read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':
    raise SystemExit(0)
archive = stage/'xctest-prepair.tar'
with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
    for p in (screen,text):
        tar.add(p,arcname=p.name)
try:
    subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER',
                    '-in',str(archive),'-out',str(base/'ios-xctest-prepair.p7m'),
                    str(Path(__file__).resolve().parent/'recipient.pem')],check=True)
finally:
    archive.unlink(missing_ok=True)
