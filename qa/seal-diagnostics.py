#!/usr/bin/env python3
"""Publish only an encrypted, redacted excerpt. Excludes pairing, UI trees and images."""
import os,re,subprocess,sys
from pathlib import Path
temp=Path(os.environ['RUNNER_TEMP']);stage=temp/'garden-qa';src=Path(__file__).resolve().parent
if not (stage/'build.log').is_file():sys.exit(0)
rules=['error:','failed','exception','could not','what went wrong','incompatible','missing','no matching','(60) ssl','not found','resource busy','no space','sigkill']
selected=[]
for l in (stage/'build.log').open(errors='replace'):
 if len(l)>3000 or any(x in l.casefold() for x in ('token=','authorization:','bearer ','cookie:','private key','api_key','api-key','password')):continue
 if any(x in l.casefold() for x in rules):
  for name in ('GITHUB_WORKSPACE','RUNNER_TEMP','HOME'):
   path=os.environ.get(name)
   if path:l=l.replace(path,'<'+name.lower()+'>')
  selected.append(l.rstrip()[:450])
if not selected:sys.exit(0)
plain=stage/'build-summary.txt';plain.write_text('\n'.join(selected[-90:])+'\n');out=temp/(sys.argv[1]+'-diagnostics.p7m')
subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(plain),'-out',str(out),str(src/'recipient.pem')],check=True);plain.unlink()
