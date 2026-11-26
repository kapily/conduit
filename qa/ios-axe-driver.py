#!/usr/bin/env python3
"""Drive the installed app on a dedicated GitHub-hosted simulator. Logs contain stage names only."""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import sys
import time
import uuid

NATIVE = 'Give me a checklist for a tray of basil seedlings.'
NATIVE_REPLY = 'Check the soil again tomorrow.'
WEB = 'Which marker belongs in the arugula tray?'
WEB_REPLY = 'Put the copper marker in the arugula tray.'
ORIGIN = 'http://127.0.0.1:43671/'
OPEN = 'Open Harness workspace'
EMPTY = 'No conversations yet'


class Failed(Exception):
    pass


def norm(raw):
    return ' '.join(str(raw or '').replace('…', '...').split()).casefold()


@dataclass
class Item:
    kind: str
    texts: tuple[str, ...]
    box: tuple[float, ...] | None = None
    ref: tuple[str, str] | None = None
    web: bool = False

    def exact(self, *labels):
        return any(norm(l) in self.texts for l in labels)

    def contains(self, text):
        return any(norm(text) in s for s in self.texts)

    @property
    def field(self):
        return any(k in self.kind.replace(' ', '') for k in ('textfield','textarea','textview','textbox','searchfield','editabletext','securetextfield'))

    def rank(self):
        return (0 if 'button' in self.kind or self.field else 1,
                self.box[1] if self.box else 0, self.box[0] if self.box else 0)


def read_box(f):
    if isinstance(f, dict):
        f = {**f.get('origin', {}), **f.get('size', {}), **f}
        if all(k in f for k in ('x','y','width','height')):
            box = tuple(float(f[k]) for k in ('x','y','width','height'))
        else:
            return None
    else:
        parts = re.findall(r'-?\d+(?:\.\d+)?', str(f or ''))
        if len(parts) != 4:
            return None
        box = tuple(map(float, parts))
    return box if box[2] > 0 and box[3] > 0 else None


def raw_tree(tree):
    items = []
    def walk(n, web=False):
        if isinstance(n, list):
            for child in n:
                walk(child, web)
            return
        if not isinstance(n, dict):
            return
        if n.get('isVisible') is False or n.get('enabled') is False or n.get('AXEnabled') is False:
            return
        kind = norm(' '.join(str(n.get(k) or '') for k in ('type','role','role_description','AXRoleDescription')))
        web = web or any(k in kind.replace(' ','') for k in ('webview','webarea'))
        parts = [str(n[k]) for k in ('AXLabel','AXValue','AXPlaceholderValue','AXTitle','AXUniqueId','label','value','placeholder') if isinstance(n.get(k), str)]
        texts = tuple(dict.fromkeys(norm(val) for part in parts for val in [part, *part.splitlines()] if norm(val)))
        box = read_box(n.get('frame') or n.get('AXFrame'))
        if box:
            items.append(Item(kind, texts, box, web=web))
        for k in ('children','elements','AXChildren'):
            if isinstance(n.get(k), list):
                walk(n[k], web)
    walk(tree)
    return items


def agent_tree(tree):
    capture = tree.get('data', {}).get('capture', {})
    sid = capture.get('snapshotID')
    if not sid:
        return []
    result = []
    for row in capture.get('rows', []):
        cols = row.split('|')
        if len(cols) != 5:
            continue
        ref, actions, role, label, val = cols
        texts = tuple(norm(s) for s in (label, val) if s)
        result.append(Item(norm(role)+' '+norm(actions),texts,ref=(sid,ref),web=ref.startswith('w')))
    return result


def button(*labels):
    return lambda n:n.exact(*labels) and not n.field


def has(text):
    return lambda n:n.contains(text)


def editor(n):
    return not n.web and (n.field or n.exact('Message...')) and not any(n.contains(x) for x in ('address','token','search'))


def web_editor(n):
    return any(n.contains(x) for x in ('Message or run a task','/ commands, @ files','Message DeepSeek')) or n.web and n.field


def field(items, secure):
    fields = sorted((n for n in items if n.field and not n.web), key=Item.rank)
    is_secret = lambda n:'secure' in n.kind or n.contains('Launch token') or n.contains('password')
    candidates = [n for n in fields if is_secret(n) == secure]
    if len(candidates) == 1:
        return candidates[0]
    # Some Flutter versions expose two generic fields underneath labeled headings.
    if len(fields) == 2 and all(n.box for n in fields):
        ordered = sorted(fields, key=lambda n:n.box[1])
        if ordered[0].box[1] < ordered[1].box[1]:
            return ordered[int(secure)]
    return None


class Driver:
    def __init__(self):
        self.stage = Path(os.environ['QA_STAGE']).resolve()
        if (os.environ.get('GITHUB_ACTIONS'),os.environ.get('RUNNER_ENVIRONMENT')) != ('true','github-hosted'):
            raise Failed('Requires the dedicated hosted runner')
        if self.stage != Path(os.environ['RUNNER_TEMP']).resolve() / 'garden-qa':
            raise Failed('Unexpected stage')
        self.uid = str(uuid.UUID(os.environ['QA_DEVICE'])).upper()
        if self.uid != (self.stage/'ios-device').read_text().strip().upper():
            raise Failed('Unexpected simulator')
        self.axe = self.stage/'axe-2.2.0/axe'
        self.out = self.stage/'private-maestro'
        self.out.mkdir(mode=0o700, exist_ok=True)
        expected = ['chooser.png','pairing.png','paired-proof.png','transcript.png','workspace.png','garden-session.mp4']
        if any((self.out/n).exists() for n in expected):
            raise Failed('Media must start in a fresh allowlist directory')
        app = self.stage/'app-build/Conduit.app/Info.plist'
        self.bundle = plistlib.loads(app.read_bytes())['CFBundleIdentifier']
        if self.bundle != 'app.cogwheel.conduit.debug':
            raise Failed('Wrong app bundle')
        self.token = (self.stage/'token.private').read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_.~=-]{8,256}', self.token):
            raise Failed('Unexpected generated fixture token format')
        self.movie = None
        self.phase = 'validate installed simulator'
        self.web = False
        self.bounds = None

    def command(self, args, stdin=None, timeout=20):
        try:
            r = subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Failed(self.phase + ': automation timeout') from None
        if r.returncode:
            raise Failed(self.phase + ': automation command failed')
        return r.stdout

    def sim(self, *args):
        return self.command(['xcrun','simctl',*args])

    def ax(self,*args,stdin=None):
        return self.command([str(self.axe),*args,'--udid',self.uid], stdin=stdin)

    def check_guard(self):
        inventory = json.loads(self.sim('list','devices','--json'))
        target = [d for ds in inventory['devices'].values() for d in ds if d['udid'].upper()==self.uid]
        if len(target)!=1 or target[0]['name']!='Garden QA' or target[0]['state']!='Booted':
            raise Failed('Requires the newly booted Garden QA simulator')
        self.sim('get_app_container',self.uid,self.bundle,'app')

    def items(self, agent=False):
        if agent:
            try:
                return agent_tree(json.loads(self.ax('describe-ui','--agent','--web','--view','reading')))
            except (Failed,ValueError):
                return []
        nodes = raw_tree(json.loads(self.ax('describe-ui','--minimal-keys')))
        for n in nodes:
            if 'application' in n.kind and n.box[2] > 200:
                self.bounds = n.box
                break
        if not self.bounds:
            windows=[n for n in nodes if 'window' in n.kind and n.box[2]>200]
            if windows:self.bounds=max(windows,key=lambda n:n.box[2]*n.box[3]).box
        if not self.bounds:
            return nodes
        x,y,w,h = self.bounds
        return [n for n in nodes if x<=n.box[0]+n.box[2]/2<=x+w and y<=n.box[1]+n.box[3]/2<=y+h]

    def tap(self,n):
        if n.ref:
            self.ax('tap','--agent','--snapshot-id',n.ref[0],'--ref',n.ref[1])
        else:
            x,y,w,h=n.box
            self.ax('tap','-x',str(round(x+w/2,1)),'-y',str(round(y+h/2,1)))
        time.sleep(.30)

    def type(self,value):
        self.ax('type','--layout','us','--stdin',stdin=value)

    def choose(self,predicate, nodes):
        return next(iter(sorted((n for n in nodes if predicate(n)),key=Item.rank)), None)

    def wait(self,predicate,*,seconds=30, tap=False, dialogs=(),scroll=False, optional=False):
        end=time.monotonic()+seconds
        swipes=0
        while time.monotonic()<end:
            nodes=self.items()
            n=self.choose(predicate,nodes)
            if n is None and self.web:
                n=self.choose(predicate,self.items(agent=True))
            if n:
                if tap: self.tap(n)
                return n
            dialog=self.choose(button(*dialogs),nodes) if dialogs else None
            if dialog:
                self.tap(dialog)
            elif scroll and self.bounds and swipes<3:
                x,y,w,h=self.bounds
                self.ax('swipe','--start-x',str(round(x+w*.5)),'--start-y',str(round(y+h*.77)),'--end-x',str(round(x+w*.5)),'--end-y',str(round(y+h*.33)),'--duration','.35')
                swipes+=1
            time.sleep(.3)
        if optional:
            return None
        raise Failed(self.phase + ': expected control missing')

    def safe(self,nodes):
        for n in nodes:
            for label in n.texts:
                if self.token.casefold() in label or any(k in label for k in ['token=','dsh-auth-','/users/','/volumes/','/home/','/private/var/','/var/folders/']):
                    raise Failed(self.phase+': private-looking label')

    def shot(self,name):
        self.safe(self.items())
        self.sim('io',self.uid,'screenshot',str(self.out/(name+'.png')))

    def start(self):
        self.movie=subprocess.Popen(['xcrun','simctl','io',self.uid,'recordVideo','--codec=h264',str(self.out/'garden-session.mp4')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(.9)
        if self.movie.poll() is not None:
            raise Failed('Simulator recording failed to start')

    def stop(self,keep):
        if self.movie:
            if self.movie.poll() is not None and keep:
                raise Failed('Simulator recording ended before verification finished')
            self.movie.send_signal(signal.SIGINT)
            try:
                self.movie.wait(timeout=22)
            except subprocess.TimeoutExpired:
                self.movie.kill();self.movie.wait()
                raise Failed('Simulator recording did not finish') from None
        output=self.out/'garden-session.mp4'
        if not keep:
            output.unlink(missing_ok=True)
        elif not output.exists() or output.stat().st_size<12000:
            raise Failed('Missing simulator recording')

    def phase_start(self,label):
        self.phase=label
        print('iOS QA:',label,flush=True)

    def pair(self):
        self.phase_start('start at backend chooser')
        self.sim('launch',self.uid,self.bundle)
        self.wait(lambda n:n.contains('DeepSeek Harness') or n.contains('Open WebUI'),seconds=40)
        n=self.wait(lambda n:n.contains('DeepSeek Harness') and not n.field,scroll=True)
        self.shot('chooser');self.tap(n)
        self.phase_start('pair with synthetic local workspace')
        self.wait(button('Connect'))
        nodes=self.items()
        url,token=field(nodes,False),field(nodes,True)
        if not url or not token or url.box==token.box:
            raise Failed('Cannot distinguish the two blank sign-in fields')
        self.shot('pairing');self.tap(url);self.type(ORIGIN)
        self.ax('key','40')
        # Next focuses the secure field, including layouts that scroll it above the keyboard.
        secret=field(self.items(),True)
        if not secret:
            raise Failed('Secure sign-in field absent')
        self.tap(secret);self.type(self.token);self.ax('key','40')
        self.wait(has(EMPTY),seconds=50,dialogs=('Connect','Allow','OK'))
        self.shot('paired-proof')

    def verify_native(self):
        self.wait(has(NATIVE),seconds=15)
        self.wait(has(NATIVE_REPLY),seconds=30)

    def return_list(self):
        self.wait(button('Chats','Back to chats','Back to sessions'),tap=True)
        self.wait(button(OPEN),seconds=12)

    def reopen(self):
        # Both hosted chats may get similar generated titles. Verify the actual native history.
        def known(n):
            return any(n.contains(s) for s in ('Seedling notes','Give me a checklist','Arugula tray','Which marker belongs','Untitled'))
        end=time.monotonic()+18
        candidates=[]
        while time.monotonic()<end and not candidates:
            nodes=self.items()
            candidates=[n for n in nodes if known(n)]
            bar=self.choose(button(OPEN),nodes)
            if bar and bar.box and not any(n.exact(EMPTY) for n in nodes):
                # An untitled session has the same name as the compose toolbar button.
                below=bar.box[1]+bar.box[3]+2
                candidates.extend(n for n in nodes if n.exact('New Chat') and n.box and n.box[1]>below)
            if not candidates:time.sleep(.35)
        for n in sorted(candidates,key=Item.rank)[:5]:
            self.tap(n)
            if self.wait(has(NATIVE),seconds=4,optional=True):
                self.verify_native()
                return
            self.return_list()
        raise Failed(self.phase + ': original native session could not be reopened')

    def run(self):
        self.check_guard(); self.pair()
        self.phase_start('native send and full streamed reply')
        self.start(); success=False
        try:
            self.wait(button('New Chat'),tap=True)
            self.wait(editor,dialogs=('Seedling notebook',),tap=True)
            self.type(NATIVE);self.wait(button('Send'),tap=True)
            self.verify_native(); self.shot('transcript')
            self.phase_start('reopen native transcript');self.return_list();self.reopen();self.return_list()
            self.phase_start('send inside official web workspace')
            self.wait(button(OPEN),tap=True);self.web=True
            self.wait(web_editor,seconds=48,tap=True,dialogs=('Continue','Allow','OK'))
            self.type(WEB);self.wait(button('Send message'),tap=True)
            self.wait(has(WEB_REPLY),seconds=45);self.shot('workspace')
            self.wait(lambda n:not n.web and n.exact('Back','Back to Conduit','Chats'),tap=True);self.web=False
            self.wait(button(OPEN),seconds=15);success=True
        finally:
            self.stop(success)
        self.phase_start('cold launch and restore native conversation')
        self.sim('terminate',self.uid,self.bundle);self.sim('launch',self.uid,self.bundle)
        self.wait(button(OPEN),seconds=40);self.reopen()
        print('iOS AXe verified the native and web replies and restored native history.',flush=True)


if __name__=='__main__':
    os.umask(0o077)
    try:
        Driver().run()
    except Failed as e:
        sys.exit(str(e))
    except Exception as e:
        sys.exit('iOS QA failed ('+type(e).__name__+').')
