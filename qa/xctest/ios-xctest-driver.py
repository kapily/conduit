#!/usr/bin/env python3
"""Host-only runner for the independent native XCTest against the cached Conduit simulator app."""
from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid


class Scenario:
    order = ['phase-chooser','chooser','pairing','fixture-token','paired','record','transcript','reply','native','phase-workspace','workspace']
    def __init__(self,stage,uid):
        self.stage=stage
        self.uid=uid
        self.out=stage/'private-maestro'
        self.out.mkdir(mode=0o700,exist_ok=True)
        self.position=0
        self.finished=False
        self.recorder=None
        self.lock=threading.Lock()
        self.last_checkpoint='preparing test'
        self.key=secrets.token_hex(32)

    def sim(self,*args):
        subprocess.run(['xcrun','simctl',*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)

    def shot(self,name):
        self.sim('io',self.uid,'screenshot',str(self.out/(name+'.png')))
        if (self.out/(name+'.png')).stat().st_size<1000:raise ValueError('short image')

    def start(self):
        path=self.out/'garden-session.mp4'
        if path.exists():raise ValueError('existing recording')
        self.recorder=subprocess.Popen(['xcrun','simctl','io',self.uid,'recordVideo','--codec=h264',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(.75)
        if self.recorder.poll() is not None:raise ValueError('recording unavailable')

    def stop(self,require=True):
        if self.recorder is None:
            if require:raise ValueError('not recording')
            return
        recorder=self.recorder;self.recorder=None
        if recorder.poll() is not None and require:raise ValueError('recording ended early')
        if recorder.poll() is None:
            recorder.send_signal(signal.SIGINT)
            try:recorder.wait(25)
            except subprocess.TimeoutExpired:
                recorder.kill();recorder.wait()
                if require:raise ValueError('recording did not close')

    def preserve(self,kind):
        source=self.out/'garden-session.mp4' if kind=='reply' else self.stage/'reply-session.mp4'
        response=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type:format=duration','-of','json',str(source)],check=True,capture_output=True,timeout=30)
        info=json.loads(response.stdout)
        if source.stat().st_size<12000 or float(info['format']['duration'])<1 or not any(s.get('codec_type')=='video' for s in info['streams']):
            raise ValueError('recording validation')
        dest=self.stage/(kind+'-session.mp4');tmp=dest.with_suffix('.tmp')
        shutil.copyfile(source,tmp);tmp.replace(dest)
        self.mark(kind+'-verified','verified')

    def mark(self,name,text):
        path=self.stage/name
        tmp=path.with_suffix('.tmp');tmp.write_text(text);tmp.replace(path)

    def action(self,name):
        with self.lock:
            if self.finished:raise ValueError('finished')
            if self.position<len(self.order):
                if name!=self.order[self.position]:raise ValueError('out of sequence')
            elif name not in ('web-sent','web-history'):
                raise ValueError('unknown web proof')
            answer=b'ok'
            if name in ('chooser','pairing','paired','transcript','workspace'):
                self.shot('paired-proof' if name=='paired' else name)
            elif name=='fixture-token':
                answer=(self.stage/'token.private').read_bytes().strip()
                if not re.fullmatch(rb'[a-zA-Z0-9._~%=-]{8,256}',answer):raise ValueError('fixture token')
            elif name=='record':self.start()
            elif name=='reply':
                self.stop();self.preserve('reply')
            elif name=='native':self.preserve('native')
            elif name in ('web-sent','web-history'):
                self.mark('web-verification.txt','sent-second-prompt' if name=='web-sent' else 'read-native-history')
                self.finished=True
            self.position+=1
            self.last_checkpoint=name
            print('iOS XCTest checkpoint:',name,flush=True)
            return answer


def server_for(scenario):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*unused):pass
        def do_POST(self):
            if self.client_address[0]!='127.0.0.1' or not hmac.compare_digest(self.headers.get('X-Garden-Runner',''),scenario.key):
                self.send_error(403);return
            try:body=scenario.action(self.path.removeprefix('/'))
            except Exception:
                self.send_error(409);return
            self.send_response(200)
            self.send_header('Content-Type','text/plain')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    return HTTPServer(('127.0.0.1',0),Handler)


def generate(stage,src):
    ruby=shutil.which('ruby')
    options=[(ruby,os.environ.copy())]
    brew=shutil.which('brew')
    if brew:
        def prefix(p):
            return subprocess.run([brew,'--prefix',p],capture_output=True,text=True).stdout.strip()
        cocoa,rb=prefix('cocoapods'),prefix('ruby')
        if cocoa and rb:
            gems=str(Path(cocoa)/'libexec')
            options.append((str(Path(rb)/'bin/ruby'),dict(os.environ,GEM_HOME=gems)))
    for exe,env in options:
        if exe and Path(exe).is_file() and subprocess.run([exe,'-e','require "xcodeproj"'],env=env,capture_output=True).returncode==0:
            subprocess.run([exe,str(src/'generate-project.rb'),str(stage)],env=env,check=True,capture_output=True)
            return
    raise RuntimeError('CocoaPods xcodeproj not available')


def main():
    if (os.environ.get('GITHUB_ACTIONS'),os.environ.get('RUNNER_ENVIRONMENT'))!=('true','github-hosted'):
        sys.exit('Requires a disposable hosted runner')
    os.umask(0o077)
    stage=Path(os.environ['QA_STAGE']).resolve()
    if stage!=(Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa'):sys.exit('Wrong runner stage')
    uid=str(uuid.UUID(os.environ['QA_DEVICE'])).upper()
    if (stage/'ios-device').read_text().strip().upper()!=uid:sys.exit('Wrong device')
    inv=json.loads(subprocess.check_output(['xcrun','simctl','list','devices','--json']))
    if not any(d['udid'].upper()==uid and d['name']=='Garden QA' and d['state']=='Booted' for devices in inv['devices'].values() for d in devices):
        sys.exit('Missing dedicated simulator')
    info=plistlib.loads((stage/'app-build/Conduit.app/Info.plist').read_bytes())
    if info['CFBundleIdentifier']!='app.cogwheel.conduit.debug':sys.exit('Wrong cached product')
    installed=subprocess.run(['xcrun','simctl','get_app_container',uid,info['CFBundleIdentifier'],'app'],capture_output=True)
    if installed.returncode:sys.exit('Cached app missing from simulator')
    src=Path(__file__).resolve().parent
    work=stage/'private-xctest'
    work.mkdir(mode=0o700)
    for name in ('AppDelegate.swift','HarnessMobileUITests.swift'):shutil.copyfile(src/name,work/name)
    scenario=Scenario(stage,uid)
    server=server_for(scenario);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    (work/'Bootstrap.plist').write_bytes(plistlib.dumps({'endpoint':f'http://127.0.0.1:{server.server_port}','key':scenario.key}))
    def on_term(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,on_term)
    job=None
    try:
        generate(work,src)
        cmd=['xcodebuild','test','-project',str(work/'HarnessQA.xcodeproj'),'-scheme','HarnessQA','-configuration','Debug',
             '-destination','platform=iOS Simulator,id='+uid,'-destination-timeout','90','-parallel-testing-enabled','NO',
             '-maximum-parallel-testing-workers','1','-jobs','1','-derivedDataPath',str(work/'DerivedData'),
             '-resultBundlePath',str(work/'Results.xcresult'),'-only-testing:HarnessProbeUITests/HarnessMobileUITests/testInstalledConduit',
             'COMPILER_INDEX_STORE_ENABLE=NO','ENABLE_CODE_COVERAGE=NO']
        with (work/'xcodebuild.private.log').open('wb') as log:
            job=subprocess.Popen(cmd,stdout=log,stderr=log,start_new_session=True)
            try:code=job.wait(timeout=880)
            finally:
                if job.poll() is None:
                    os.killpg(job.pid,signal.SIGTERM)
                    try:job.wait(timeout=15)
                    except subprocess.TimeoutExpired:os.killpg(job.pid,signal.SIGKILL);job.wait()
        if code or not scenario.finished:
            if scenario.last_checkpoint=='preparing test':
                # Only compiler diagnostics from the two known synthetic Swift files may be printed.
                for line in (work/'xcodebuild.private.log').read_text(errors='replace').splitlines():
                    found=re.search(r'(HarnessMobileUITests|AppDelegate)[.]swift:[0-9]+:[0-9]+: error: .+',line)
                    if found and len(found[0])<360 and all(c not in found[0] for c in ('/','@','?','=')):
                        print(found[0],flush=True)
            sys.exit('XCTest stopped after verified stage: '+scenario.last_checkpoint+'. XCTest output remains private.')
    finally:
        server.shutdown();server.server_close();scenario.stop(require=False)
        # XCTest can include screenshots, host paths and keyboard diagnostics. Only
        # the explicitly generated simulator media and checkpoint markers go to the sealer.
        (work/'Bootstrap.plist').unlink(missing_ok=True)


if __name__=='__main__':
    main()
