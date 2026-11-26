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
        self.lock=threading.Lock()

    def request(self,client,host,cap,origin,path,size):
        if client!='127.0.0.1' or host!=f'127.0.0.1:{PORT}' or origin is not None or size!='0' or not hmac.compare_digest(cap,self.nonce):
            return 403,b''
        with self.lock:
            expected={'initial':'credential','credential':'paired','paired':'reply','reply':'history'}.get(self.phase)
            if path!='/'+str(expected) or expected is None:return 409,b''
            if expected=='credential':
                data=self.token.encode()
            else:
                getattr(self.media,expected)()
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
        self.out=stage/'integration-verified'
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

    def paired(self):
        if self.proc is not None:raise Failure('Recording already started.')
        if self.platform=='android':
            cmd=[*self.adb,'shell',f'echo $$ > {self.remote}.pid && exec screenrecord --size 720x1600 --bit-rate 4000000 --time-limit 180 {self.remote}.mp4']
        else:cmd=['xcrun','simctl','io',self.device,'recordVideo','--codec=h264',str(self.raw)]
        self.proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(1)
        if self.proc.poll() is not None:raise Failure('Recording did not start.')

    def screenshot(self,label):
        dest=self.out/(self.platform+'-'+label+'.png')
        if self.platform=='android':dest.write_bytes(run(*self.adb,'exec-out','screencap','-p'))
        else:run('xcrun','simctl','io',self.device,'screenshot',str(dest))
        if dest.stat().st_size<1000 or not dest.read_bytes()[:8]==b'\x89PNG\r\n\x1a\n':
            raise Failure('Invalid capture.')

    def stop(self):
        if self.proc is None:return
        if self.proc.poll() is not None:raise Failure('Recording terminated before confirmation.')
        if self.platform=='ios':self.proc.send_signal(signal.SIGINT)
        else:
            pid=run(*self.adb,'shell','cat',self.remote+'.pid').strip()
            if not pid.isdigit() or (self.remote+'.mp4').encode() not in run(*self.adb,'shell','cat','/proc/'+pid.decode()+'/cmdline').split(b'\x00'):
                raise Failure('Recording owner changed.')
            run(*self.adb,'shell','kill','-2',pid.decode())
        self.proc.wait(timeout=20);self.proc=None

    def reply(self):
        self.screenshot('transcript')
        self.stop()
        if self.platform=='android':run(*self.adb,'pull',self.remote+'.mp4',str(self.raw))
        target=self.out/(self.platform+'-native-garden.mp4')
        run('ffmpeg','-nostdin','-loglevel','error','-i',str(self.raw),'-map','0:v:0','-an','-map_metadata','-1','-vf','scale=720:-2',
            '-c:v','libx264','-threads','2','-crf','23','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',str(target),timeout=125)
        data=json.loads(run('ffprobe','-v','error','-show_entries','stream=codec_name,codec_type:format=duration','-of','json',str(target)))
        if len(data['streams'])!=1 or data['streams'][0]['codec_name']!='h264' or float(data['format']['duration'])<2:
            raise Failure('Incomplete device video.')
        (self.out/'reply-verified').write_text('Native user message and complete server response verified.\n')

    def history(self):
        self.screenshot('history')
        (self.out/'history-verified').write_text('The single freshly created server session reopened in the native interface.\n')

    def cleanup(self):
        if self.proc:
            with contextlib.suppress(Exception):self.stop()
            if self.proc and self.proc.poll() is None:
                self.proc.terminate();self.proc.wait(timeout=5)
        self.raw.unlink(missing_ok=True)
        if self.platform=='android':
            run(*self.adb,'shell','rm','-f',self.remote+'.pid',self.remote+'.mp4')
            run(*self.adb,'reverse','--remove',f'tcp:{PORT}')


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
    if stage.joinpath('integration-verified').exists() or run('git','rev-parse','HEAD').decode().strip()!=os.environ['QA_COMMIT']:
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
        if proc.returncode or receipt.phase!='history':
            raise Failure('Device integration failed after fixed phase '+receipt.phase+'.')
        print('Verified native pairing, full reply and reopened server history in the production widget tree.',flush=True)
    finally:
        if server:server.shutdown();server.server_close()
        media.cleanup()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,Failure) else 'Hosted device verification failed ('+type(error).__name__+').',file=sys.stderr)
        sys.exit(1)
