#!/usr/bin/env python3
"""Run only in the private, disposable hosted GardenQA build workspace."""
import contextlib
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

PORT=43673
APP='app.cogwheel.conduit.debug'


class Failure(Exception):pass


def run(*args,timeout=45):
    proc=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout)
    if proc.returncode:raise Failure('Hosted helper failed.')
    return proc.stdout


class Receipt:
    def __init__(self,token,nonce,media):
        self.token=token
        self.nonce=nonce
        self.media=media
        self.phase='initial'
        self.authenticated=False
        self.lock=threading.Lock()

    def request(self,client,host,cap,origin,path,size):
        if client!='127.0.0.1' or host!=f'127.0.0.1:{PORT}' or origin is not None or size!='0' or not hmac.compare_digest(cap,self.nonce):
            return 403,b''
        with self.lock:
            if path.startswith('/mark-'):
                mark=path[6:]
                if mark in {'grant-stored','grant-unavailable','grant-unauthorized','grant-invalidAddress','grant-invalidResponse','grant-untrustedHost','typed','connect-visible','connect-pressed','form-gone','catalog','pair-rejected','pair-unavailable','provider-error','recorder-error','wait-Connect','wait-authenticated-sky-sessions','unauthenticated-empty-catalog','missing-session','directory-picker','workspace-catalog'}:
                    print('Synthetic iOS verification checkpoint: '+mark,flush=True)
                    return 200,b'ok'
                return 409,b''
            expected={'initial':'credential','credential':'paired','paired':'selected','selected':'folders','folders':'verified','verified':'restored'}.get(self.phase)
            if path!='/'+str(expected) or expected is None:return 409,b''
            if expected=='credential':
                data=self.token.encode()
            else:
                if expected=='paired':
                    self.authenticated=True
                    print('Authenticated synthetic workspace catalog confirmed before recording.',flush=True)
                try:
                    getattr(self.media,expected)()
                except Exception as exc:
                    print('Hosted '+expected+' controller failed ('+type(exc).__name__+').',flush=True)
                    raise
                data=b'ok'
            self.phase=expected
            return 200,data


def serve(receipt,port=PORT):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                code,data=receipt.request(self.client_address[0],self.headers.get('Host',''),self.headers.get('X-Garden-Run',''),
                    self.headers.get('Origin'),self.path,self.headers.get('Content-Length'))
            except Exception:
                code,data=500,b''
            self.send_response(code);self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    return server


class Media:
    def __init__(self,platform,device,stage):
        self.stage=stage
        self.platform=platform
        self.device=device
        self.out=stage/'workspace-evidence'
        self.out.mkdir(mode=0o700)
        self.raw=stage/'widget-recording.private.mp4'
        self.proc=None
        if platform=='android':
            if device!='emulator-5554' or run('adb','-s',device,'emu','avd','name').splitlines()[:1]!=[b'GardenQA']:
                raise Failure('Requires the newly created GardenQA AVD.')
            self.adb=['adb','-s',device]
            run(*self.adb,'reverse',f'tcp:{PORT}',f'tcp:{PORT}')
            if not all(f'tcp:{p} tcp:{p}'.encode() in run(*self.adb,'reverse','--list') for p in (PORT,43671)):
                raise Failure('Missing private Android fixture reverse port.')
            self.remote='/data/local/tmp/garden-widget-'+str(os.getpid())
        elif platform=='ios':
            if str(uuid.UUID(device)).upper()!=(stage/'ios-device').read_text().strip().upper():
                raise Failure('Requires the newly created GardenQA iOS Simulator.')
        else:raise Failure('Unknown hosted platform.')


    def screenshot(self,label):
        dest=self.out/(label+'.png')
        run('xcrun','simctl','io',self.device,'screenshot',str(dest))
        if dest.stat().st_size<1000 or dest.read_bytes()[:8]!=bytes.fromhex("89504e470d0a1a0a"): raise Failure('Invalid iPhone workspace image.')
    def paired(self):
        self.proc=subprocess.Popen(['xcrun','simctl','io',self.device,'recordVideo','--codec=h264',str(self.raw)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(1)
        if self.proc.poll() is not None:raise Failure('Simulator recording did not start.')
    def stop_record(self):
        if self.proc is None or self.proc.poll() is not None:raise Failure('No active simulator recording.')
        self.proc.send_signal(signal.SIGINT)
        self.proc.wait(timeout=25);self.proc=None

    def selected(self):
        self.screenshot('selected')
    def folders(self):
        self.screenshot('workspaces')
        (self.out/'switch-verified').write_text(os.environ['QA_COMMIT'])
    def verified(self):
        self.screenshot('created')
        time.sleep(1)
        self.stop_record()
        run('ffmpeg','-nostdin','-loglevel','error','-i',str(self.raw),'-map','0:v:0','-an','-map_metadata','-1',
            '-vf','scale=720:-2','-c:v','libx264','-threads','2','-crf','23','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',
            str(self.out/'workspaces.mp4'),timeout=130)
    def restored(self):
        (self.out/'verified').write_text(os.environ['QA_COMMIT'])
    def cleanup(self):
        if self.proc and self.proc.poll() is None:
            with contextlib.suppress(Exception):self.stop_record()
            if self.proc and self.proc.poll() is None:
                self.proc.kill();self.proc.wait(timeout=5)
        self.raw.unlink(missing_ok=True)


def prepare(checkout,source):
    tests=checkout/'integration_test'
    tests.mkdir(exist_ok=True)
    dest=tests/'garden_device_test.dart'
    if dest.exists():raise Failure('The hosted fixture target already exists.')
    spec=checkout/'pubspec.yaml'
    data=spec.read_text()
    if data.count('\ndev_dependencies:\n')!=1 or '\n  integration_test:' in data:raise Failure('Unexpected dependency layout.')
    spec.write_text(data.replace('\ndev_dependencies:\n','\ndev_dependencies:\n  integration_test:\n    sdk: flutter\n',1))
    shutil.copyfile(source/'garden_device_test.dart',dest)


def main():
    os.umask(0o077)
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise Failure('This entry point requires a fresh hosted runner.')
    checkout=Path.cwd().resolve();stage=Path(os.environ['QA_STAGE']).resolve()
    if checkout!=Path(os.environ['GITHUB_WORKSPACE']).resolve()/'conduit' or stage!=Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa':
        raise Failure('Unexpected checkout or synthetic fixture stage.')
    if stage.joinpath('workspace-evidence').exists() or run('git','rev-parse','HEAD').decode().strip()!=os.environ['QA_COMMIT']:
        raise Failure('Expected fresh checkout at the selected feature commit.')
    platform=sys.argv[1];device=os.environ['QA_DEVICE']
    token=(stage/'token.private').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9._~=-]{16,512}',token):raise Failure('Invalid isolated fixture credential.')
    prepare(checkout,Path(__file__).resolve().parent)
    nonce=secrets.token_hex(24)
    media=Media(platform,device,stage)
    receipt=Receipt(token,nonce,media);server=None
    try:
        server=serve(receipt)
        with (stage/'flutter-integration.private.log').open('wb') as log:
            deps=subprocess.run(['flutter','pub','get'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,timeout=280)
            if deps.returncode:raise Failure('Flutter integration dependencies failed.')
            command=['flutter','test','--no-pub','--reporter','expanded','-d',device,'--dart-define=GARDEN_RUN='+nonce,'integration_test/garden_device_test.dart']
            proc=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,timeout=2400)
        if proc.returncode or receipt.phase!='restored':
            safe=[]
            for line in (stage/'flutter-integration.private.log').read_text(errors='ignore').splitlines():
                text=line.strip()
                if re.fullmatch(r'(Bad state: )?(Local garden controller rejected [a-z]+\.|Garden check did not reach [A-Za-z ]+\.|Missing catalog navigation\.)',text):
                    safe.append(text)
            if safe:print('Workspace check: '+safe[-1],flush=True)
            full=(stage/'flutter-integration.private.log').read_text(errors='ignore')
            for hint in ['dismissed pairing form','authenticated sky sessions','visible Connect','Connect','select existing lunar workspace','new workspace','restored selected workspace and scoped catalog','filtered lunar sessions','three workspace options']:
                if ('Garden check did not reach '+hint+'.') in full:print('Timed out waiting for: '+hint,flush=True)
            for pattern in ['Garden check did not reach','Local garden controller rejected','A Dart VM Service','A RenderFlex overflowed','TestFailure','StateError','SocketException','DioException','FlutterError','TimeoutException','Bad state: No element','Routing error','Test timed out','Expected:','Failed to load','failed assertion','Start Flutter debug session','was thrown','Error launching','The application failed to start','Error connecting to the service protocol','not a subtype of type','HttpException','No Material widget found','The framework has been disposed','Multiple exceptions','Expected a single matching node','was hit tested at']:
                if pattern.casefold() in full.casefold():print('Integration output classification: '+pattern,flush=True)

            if receipt.authenticated:
                with contextlib.suppress(Exception):media.screenshot('diagnostic')
            raise Failure('Device integration failed after fixed phase '+receipt.phase+'.')
        print('Verified workspace switch, server directory creation, registration, and scoped chat creation in the installed iPhone app.',flush=True)
    finally:
        if server:server.shutdown();server.server_close()
        media.cleanup()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,Failure) else 'Hosted device verification failed ('+type(error).__name__+').',file=sys.stderr)
        sys.exit(1)
