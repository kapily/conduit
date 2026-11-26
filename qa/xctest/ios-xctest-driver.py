#!/usr/bin/env python3
"""Host-only runner for the independent native XCTest against the cached Conduit simulator app."""
from __future__ import annotations
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlencode
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
        self.issued=False
        self.clipboard_pending=False
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

    prepair_points={'chooser-found','chooser-center-tapped','chooser-retapped','form-visible','field-located','before-secret'}
    public_labels=('deepseek harness','open webui','hermes agent','server address','launch token','connect','cancel','allow','continue','paste','next','done','conduit')
    def discard_diagnostic(self):
        for suffix in ('-safe','.png','.txt'):
            (self.stage/('xctest-prepair'+suffix)).unlink(missing_ok=True)

    def prepair(self,name,raw):
        if self.issued or self.position not in (1,2,3) or name not in self.prepair_points:raise ValueError('outside credential-free form')
        self.discard_diagnostic()
        lines=['checkpoint '+name]
        for line in raw.splitlines()[:700]:
            match=re.search(r'\b(Application|Window|Group|Other|Button|StaticText|TextField|SecureTextField|TextView|ScrollView|Keyboard|Alert|MenuItem|WebView|Cell)\b',line)
            if not match:continue
            labels=[w for w in self.public_labels if re.search(r'(?<![a-z])'+re.escape(w)+r'(?![a-z])',line.lower())]
            frame=re.search(r'\{\{(-?[\d.]+),\s*(-?[\d.]+)\},\s*\{([\d.]+),\s*([\d.]+)\}\}',line)
            lines.append(' '.join([match.group(1),','.join(frame.groups()) if frame else '-',','.join(labels)]))
        self.sim('io',self.uid,'screenshot',str(self.stage/'xctest-prepair.png'))
        (self.stage/'xctest-prepair.txt').write_text('\n'.join(lines)+'\n')
        self.mark('xctest-prepair-safe','verified')
        present=[w for w in self.public_labels if any(w in line for line in lines[1:])]
        print('iOS XCTest pre-pair:',name,'known labels:',','.join(present),'nodes:',len(lines)-1,flush=True)
        return b'ok'

    def clipboard(self,value):
        subprocess.run(['xcrun','simctl','pbcopy',self.uid],input=value,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        if not value:
            r=subprocess.run(['xcrun','simctl','pbpaste',self.uid],check=True,capture_output=True,timeout=30)
            if r.stdout.strip():raise ValueError('uncleared simulator pasteboard')

    def credential(self,name):
        token=(self.stage/'token.private').read_bytes().strip()
        if not re.fullmatch(rb'[a-zA-Z0-9._~%=-]{8,256}',token):raise ValueError('fixture token')
        self.discard_diagnostic();self.issued=True
        if name=='paste-link':
            self.clipboard_pending=True
            self.clipboard(('http://127.0.0.1:43671/?'+urlencode({'token':token.decode()})).encode())
            return b'ok'
        return token

    def native_visible(self):
        if not self.issued or self.clipboard_pending or self.position not in (6,8):
            raise ValueError('not checking confirmed native conversation')
        return self.ocr_visible(self.stage/'private-xctest')

    def ocr_visible(self,private,region=None):
        image=private/'native-ocr.png';script=private/'VerifyVisible.swift'
        script.write_text(r'''import Foundation
import Vision
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["en-US"]
if CommandLine.arguments.count == 6 {
    let v = CommandLine.arguments.dropFirst(2).compactMap { Double($0) }
    guard v.count == 4 else { exit(1) }
    request.regionOfInterest = CGRect(x: v[0], y: v[1], width: v[2], height: v[3])
}
let handler = VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1]))
do {
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    func normalize(_ text: String) -> String {
        text.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter{ !$0.isEmpty }.joined(separator: " ")
    }
    let text = normalize(lines.joined(separator: " "))
    let expected = ["Give me a checklist for a tray of basil seedlings", "Label each pot", "Water the seedlings", "Check the soil again tomorrow"]
    print(expected.allSatisfy{ text.contains(normalize($0)) } ? "verified" : "incomplete")
} catch { print("incomplete") }
''')
        try:
            self.sim('io',self.uid,'screenshot',str(image))
            proof=subprocess.run(['xcrun','--sdk','macosx','swift',str(script),str(image),*(str(n) for n in (region or []))],capture_output=True,timeout=58)
            return b'verified' if proof.returncode==0 and proof.stdout.strip()==b'verified' else b'incomplete'
        finally:image.unlink(missing_ok=True)

    def action(self,name,body=''):
        with self.lock:
            if self.finished:raise ValueError('finished')
            if name=='native-visible':return self.native_visible()
            if name.startswith('prepair-'):return self.prepair(name[8:],body)
            if name=='clear-clipboard' and self.issued and self.position==4:
                self.clipboard(b'');self.clipboard_pending=False;return b'ok'
            if name=='fixture-token' and self.issued and self.position==4 and not self.clipboard_pending:
                return self.credential(name)
            step='fixture-token' if name=='paste-link' else name
            if self.position<len(self.order):
                if step!=self.order[self.position]:raise ValueError('out of sequence')
            elif name not in ('web-sent','web-history'):
                raise ValueError('unknown web proof')
            answer=b'ok'
            if name in ('chooser','pairing','paired','transcript','workspace'):
                self.shot('paired-proof' if name=='paired' else name)
            elif step=='fixture-token':answer=self.credential(name)
            elif name=='record':
                if self.clipboard_pending:raise ValueError('uncleared clipboard')
                self.start()
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


class RestoredScenario(Scenario):
    """A read-only, independently seeded run; this bridge has no pairing/token endpoints."""
    def __init__(self,stage,uid):
        super().__init__(stage,uid)
        self.out=stage/'restored-evidence'
        self.out.mkdir(mode=0o700)
        self.private=stage/'private-restored-xctest'
        self.recording_name=None
        self.position=0
        self.checks=[]
        self.model_selection=None
        self.fingerprints={}
        self.ios_runtime=''
        self.commit=os.environ.get('QA_COMMIT','')
        if not re.fullmatch('[0-9a-f]{40}',self.commit):raise ValueError('Missing tested source SHA')

    def ensure_cached_product(self,installed,cached,payload):
        choices=[payload,'Frameworks/App.framework/App','Frameworks/App.framework/flutter_assets/kernel_blob.bin']
        if not (cached/choices[1]).is_file() and not (cached/choices[2]).is_file():
            raise ValueError('Expected compiled cached Flutter product')
        for item in choices:
            if not (cached/item).is_file():continue
            current=digest(installed/item)
            if current!=digest(cached/item):raise ValueError('Installed app differs from the cached product')
            self.fingerprints[item]=current

    def clip(self,name):
        self.recording_name=name
        self.recorder=subprocess.Popen(['xcrun','simctl','io',self.uid,'recordVideo','--codec=h264',str(self.out/name)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(.8)
        if self.recorder.poll() is not None:raise ValueError('Recorder did not start')

    def closeclip(self):
        self.stop()
        path=self.out/self.recording_name
        p=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type:format=duration','-of','json',str(path)],check=True,capture_output=True,timeout=30)
        media=json.loads(p.stdout)
        if not any(s.get('codec_type')=='video' for s in media['streams']) or float(media['format']['duration'])<1:raise ValueError('Missing verified movie')
        self.recording_name=None

    def restart(self):
        bundle='app.cogwheel.conduit.debug'
        def launch_pid():
            out=subprocess.check_output(['xcrun','simctl','launch',self.uid,bundle],timeout=90,stderr=subprocess.DEVNULL).decode()
            found=re.search(r':\s*([1-9][0-9]*)\s*$',out)
            if not found:raise ValueError('No installed-app process identity')
            return int(found.group(1))
        before=launch_pid()
        self.clip('ios-cold-restore.mp4')
        self.sim('terminate',self.uid,bundle)
        after=launch_pid()
        if before==after:raise ValueError('New Conduit process not established')

    def receipt(self,name,files):
        docs={
            'scenario':'independently enrolled Harness connection and existing synthetic Seedling notes session',
            'conduit_source':self.commit,'app_bundle':'app.cogwheel.conduit.debug','runtime':self.ios_runtime,
            'cached_product_sha256':self.fingerprints,'harness':'0.2.2-rc.5','automation':'Apple XCTest and simctl',
            'os_process_restart':'simctl terminate then launch; verified distinct process IDs',
            'verified_checks':self.checks,'official_workspace_session':self.model_selection,
            'files':{file:digest(self.out/file) for file in files},
            'privacy_review':'pending offline review','github_run':os.environ.get('GITHUB_RUN_ID','')
        }
        output=self.out/name;temp=output.with_suffix('.tmp')
        temp.write_text(json.dumps(docs,indent=2)+'\n');temp.replace(output)

    def action(self,name,body=''):
        with self.lock:
            if self.finished:raise ValueError('finished')
            if name=='native-visible' and self.position==1:return self.ocr_visible(self.private)
            if name=='restored-web-visible' and self.position==3:
                region=json.loads(body)
                if not isinstance(region,list) or len(region)!=4 or not all(isinstance(x,(int,float)) and 0<=x<=1 for x in region) or min(region[2:])<=0 or region[0]+region[2]>1.002 or region[1]+region[3]>1.002:raise ValueError('invalid WebKit region')
                return self.ocr_visible(self.private,region)
            expected=('restored-restart','restored-native','restored-open-web','web-proof','restored-finished')
            key='web-proof' if name in ('restored-web-selected','restored-web-already-open') else name
            if self.position>=len(expected) or key!=expected[self.position]:raise ValueError('invalid restored stage')
            if name=='restored-restart':
                self.restart()
                self.checks.append('existing app terminated and relaunched as a new process')
            elif name=='restored-native':
                self.shot('ios-native-restored');self.closeclip()
                self.checks.append('native saved title opened after process restart; original prompt and all three reply items verified')
                self.receipt('native-provenance.json',['ios-native-restored.png','ios-cold-restore.mp4'])
            elif name=='restored-open-web':self.clip('ios-workspace-restore.mp4')
            elif key=='web-proof':
                self.shot('ios-workspace-restored')
                self.model_selection='selected existing session' if name=='restored-web-selected' else 'existing session already open'
                self.checks.append('official WebKit viewport showed the same stored prompt and all three reply items')
            else:
                self.closeclip()
                self.checks.append('returned from the official workspace to the native app')
                files=['ios-native-restored.png','ios-cold-restore.mp4','ios-workspace-restored.png','ios-workspace-restore.mp4']
                self.receipt('provenance.json',files)
                self.finished=True
            self.position+=1;self.last_checkpoint=name
            print('Restored iOS XCTest checkpoint:',name,flush=True)
            return b'ok'


def digest(path):
    total=hashlib.sha256()
    with Path(path).open('rb') as contents:
        for block in iter(lambda:contents.read(262144),b''):total.update(block)
    return total.hexdigest()


def server_for(scenario):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*unused):pass
        def do_POST(self):
            if self.client_address[0]!='127.0.0.1' or not hmac.compare_digest(self.headers.get('X-Garden-Runner',''),scenario.key):
                self.send_error(403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<0 or length>180000:raise ValueError('size')
                body=scenario.action(self.path.removeprefix('/'),self.rfile.read(length).decode('utf-8',errors='replace'))
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
    if sys.argv[1:] not in ([],['--restored']):sys.exit('Unknown XCTest mode')
    restored=sys.argv[1:]==['--restored']
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
    work=stage/('private-restored-xctest' if restored else 'private-xctest')
    work.mkdir(mode=0o700)
    for name in ('AppDelegate.swift','HarnessMobileUITests.swift'):shutil.copyfile(src/name,work/name)
    scenario=RestoredScenario(stage,uid) if restored else Scenario(stage,uid)
    if restored:
        scenario.ensure_cached_product(Path(installed.stdout.decode().strip()),stage/'app-build/Conduit.app',info['CFBundleExecutable'])
        scenario.ios_runtime=next(rt.rsplit('.',1)[-1] for rt,ds in inv['devices'].items() if any(d['udid'].upper()==uid for d in ds))
    test_method='testRestoredConduit' if restored else 'testInstalledConduit'
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
             '-resultBundlePath',str(work/'Results.xcresult'),'-only-testing:HarnessProbeUITests/HarnessMobileUITests/'+test_method,
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
        if scenario.clipboard_pending:
            scenario.clipboard(b'')
        # XCTest can include screenshots, host paths and keyboard diagnostics. Only
        # the explicitly generated simulator media and checkpoint markers go to the sealer.
        (work/'Bootstrap.plist').unlink(missing_ok=True)


if __name__=='__main__':
    main()
