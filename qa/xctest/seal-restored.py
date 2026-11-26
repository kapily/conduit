#!/usr/bin/env python3
"""Seal the standalone iOS restoration receipts/media from one fresh run."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile


def hashfile(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(262144),b''):h.update(block)
    return h.hexdigest()


def seal(root,receipt,allowlist,envelope,cert,revision):
    source=root/receipt
    proof=json.loads(source.read_text())
    if proof.get('conduit_source')!=revision or set(proof.get('files',{}))!=set(allowlist):
        raise ValueError('Unexpected restoration receipt')
    if any((root/name).is_symlink() or hashfile(root/name)!=proof['files'][name] for name in allowlist):
        raise ValueError('Restored media has changed since verification')
    archive=envelope.with_suffix('.private.tar')
    try:
        with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
            for name in [receipt,*allowlist]:
                file=root/name
                entry=tar.gettarinfo(str(file),name);entry.uid=entry.gid=0;entry.uname=entry.gname='';entry.mtime=0
                with file.open('rb') as f:tar.addfile(entry,f)
        tmp=envelope.with_suffix('.tmp')
        subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(archive),'-out',str(tmp),str(cert)],check=True,capture_output=True)
        tmp.replace(envelope)
    finally:archive.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    if (os.getenv('GITHUB_ACTIONS'),os.getenv('RUNNER_ENVIRONMENT'))!=('true','github-hosted'):
        raise SystemExit('Requires hosted fixture')
    stage=Path(os.environ['QA_STAGE']).resolve()
    runner=Path(os.environ['RUNNER_TEMP']).resolve()
    if stage!=runner/'garden-qa':raise SystemExit('Unexpected verification stage')
    root=stage/'restored-evidence'
    cert=Path(__file__).resolve().parents[1]/'recipient.pem'
    pem=cert.read_bytes()
    if not pem.startswith(b'-----BEGIN CERTIFICATE-----') or b'PRIVATE KEY' in pem:raise SystemExit('Public recipient certificate required')
    native=['ios-native-restored.png','ios-cold-restore.mp4']
    full=native+['ios-workspace-restored.png','ios-workspace-restore.mp4']
    revision=os.environ['QA_COMMIT']
    if (root/'native-provenance.json').is_file():
        seal(root,'native-provenance.json',native,runner/'ios-restored-native.p7m',cert,revision)
        print('Sealed the restored native session evidence.',flush=True)
    if (root/'provenance.json').is_file():
        seal(root,'provenance.json',full,runner/'ios-restored-review.p7m',cert,revision)
        print('Sealed the restored native plus WebKit evidence.',flush=True)

if __name__=='__main__':main()
