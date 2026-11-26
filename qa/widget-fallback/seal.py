"""Seal only verified synthetic frames/clip and fixed-stage build diagnostics."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
os.umask(0o077)
platform=sys.argv[1]
if platform not in ('ios','android'):raise RuntimeError('Unexpected platform')
stage=Path(os.environ['QA_STAGE']);out=stage/'integration-verified';temp=Path(os.environ['RUNNER_TEMP'])
cert=Path(__file__).parent.parent/'recipient.pem'
if b'PRIVATE KEY' in cert.read_bytes() or not cert.read_bytes().startswith(b'-----BEGIN CERTIFICATE-----'):raise RuntimeError('Invalid public certificate.')
names=[]
if (out/'reply-verified').is_file():names+=[platform+'-native-garden.mp4',platform+'-transcript.png','reply-verified']
if (out/'history-verified').is_file():names+=[platform+'-history.png','history-verified']
with TemporaryDirectory(dir=stage) as d:
    tmp=Path(d);archive=tmp/'private.tar'
    diagnostic=stage/'flutter-integration.private.log';excerpt=''
    if diagnostic.is_file():
        data=diagnostic.read_text(errors='replace')
        allow=r'Error:|Exception:|Unable to|Failure|Failed to|doesn.t (?:define|support)|does not (?:exist|support)|Garden check did not reach|The argument type |Method not found|No named parameter|requires a higher|CocoaPods could not|BUILD FAILED|Gradle task.+failed|could not resolve|couldn.t resolve|failed to initialize|test failed|connection refused|Could not build|Unhandled exception|Could not (?:GET|HEAD|download)|status code|Read timed out|HTTP response|Resource missing|Too Many Requests|requires at least|minimum supported|Caused by:|A problem occurred (?:evaluating|configuring)'
        for line in data.splitlines():
            if re.search(allow,line,re.I) and not re.search(r'passw|cookie|bearer|launch.token|GARDEN_RUN|authorization|credential',line,re.I):
                line=re.sub(r'(?:/Users/|/home/|/Volumes/|/private/|/var/)[^\s]+','<temporary-path>',line)
                line=re.sub(r'\b[A-Za-z0-9_=-]{28,}\b','<value>',line)
                excerpt=(excerpt+line[:450]+'\n')[-6500:]
    if not names and not excerpt:sys.exit(0)
    with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as t:
        for name in names:
            if not (out/name).is_file():raise RuntimeError('Evidence file absent.')
            t.add(out/name,arcname=name,recursive=False)
        if excerpt:
            file=tmp/'build-summary.txt';file.write_text(excerpt);t.add(file,arcname=file.name)
    subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(archive),'-out',str(temp/(platform+'-widget-review.p7m')),str(cert)],check=True)
