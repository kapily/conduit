import os, subprocess,sys
from pathlib import Path
s=Path(os.environ['QA_STAGE']).resolve()
assert os.environ['GITHUB_ACTIONS']=='true' and s==Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa'
src=s/'workspace-evidence/diagnostic.png'
if src.is_file():
    assert src.stat().st_size<15*1024*1024 and src.read_bytes().startswith(bytes.fromhex('89504e470d0a1a0a'))
    cert=Path(__file__).resolve().parents[1]/'recipient.pem'
    assert b'PRIVATE KEY' not in cert.read_bytes() and sys.argv[1] in ('android','ios')
    subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(src),'-out',str(s.parent/('workspace-'+sys.argv[1]+'-diagnostic.p7m')),str(cert)],check=True)
