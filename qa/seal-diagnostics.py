#!/usr/bin/env python3
"""Publish only an encrypted, redacted excerpt. Excludes pairing, UI trees and images."""
import os,re,subprocess,sys
from pathlib import Path
temp=Path(os.environ['RUNNER_TEMP']);stage=temp/'garden-qa';src=Path(__file__).resolve().parent

rules=['error:','failed','exception','could not','what went wrong','incompatible','missing','no matching','(60) ssl','not found','resource busy','no space','sigkill']
selected=[]
source = stage/'build.log'
for l in source.read_text(errors='replace').splitlines() if source.is_file() else ():
 if len(l)>3000 or any(x in l.casefold() for x in ('token=','authorization:','bearer ','cookie:','private key','api_key','api-key','password')):continue
 if any(x in l.casefold() for x in rules):
  for name in ('GITHUB_WORKSPACE','RUNNER_TEMP','HOME'):
   path=os.environ.get(name)
   if path:l=l.replace(path,'<'+name.lower()+'>')
  selected.append(l.rstrip()[:450])
# Preserve command progress from only the fresh synthetic flow. The single
# secret is masked by exact value before allowlisted diagnostic lines are selected.
maestro=stage/'private-maestro.log'
secret=stage/'token.private'
if maestro.is_file() and secret.is_file():
 data=maestro.read_text(errors='replace').replace(secret.read_text().strip(),'<redacted>')
 data=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',data)
 for line in data.splitlines():
  if len(line)>1000 or re.search('input text|inputText|bearer |cookie|token|password|authorization|element hierarchy',line,re.I):
   continue
  if re.search('failed|assert|tap on|recording|launch app|screenshot|view hierarchy|visible|not found|timed out|timeout',line,re.I):
   for name in ('GITHUB_WORKSPACE','RUNNER_TEMP','HOME'):
    value=os.environ.get(name)
    if value:line=line.replace(value,'<'+name.lower()+'>')
   selected.append(line[:350])
if not selected:sys.exit(0)
plain=stage/'build-summary.txt';plain.write_text('\n'.join(selected[-90:])+'\n');out=temp/(sys.argv[1]+'-diagnostics.p7m')
subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-cbc','-outform','DER','-in',str(plain),'-out',str(out),str(src/'recipient.pem')],check=True);plain.unlink()
