#!/usr/bin/env python3
"""Drive the hosted GardenQA AVD with Android platform tools. No device logging."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

APP = 'app.cogwheel.conduit.debug'
ORIGIN = 'http://127.0.0.1:43671/'
QUESTION = 'Give me a checklist for a tray of basil seedlings.'
ANSWER = 'Check the soil again tomorrow.'
WEB_QUESTION = 'Which marker belongs in the arugula tray?'
WEB_ANSWER = 'Put the copper marker in the arugula tray.'
WEB_PLACEHOLDER = r'Message or run a task|Describe what you want to build|/ commands, @ files'
OPEN_WORKSPACE = 'Open Harness workspace'
FLAGS = re.I | re.S


class CheckFailed(Exception):
    """A fixed diagnostic, safe for hosted logs."""


@dataclass(frozen=True)
class Node:
    parts: tuple
    kind: str
    package: str
    secure: bool
    enabled: bool
    web: bool
    box: tuple
    focused: bool

    def exact(self, *choices):
        return any(t.casefold() == c.casefold() for t in self.parts for c in choices)

    def matches(self, pattern):
        return any(re.search(pattern, s, FLAGS) for s in self.parts)

    @property
    def edit(self):
        return self.kind.endswith('EditText')


def parse(raw):
    # Some adb builds also write a status line. Only parse a complete hierarchy.
    start, end = raw.find(b'<hierarchy'), raw.rfind(b'</hierarchy>')
    if start < 0 or end < start:
        return []
    try:
        root = ET.fromstring(raw[start:end + len(b'</hierarchy>')])
    except ET.ParseError:
        return []
    result = []
    def walk(el, package='', web=False):
        package = el.get('package') or package
        kind = el.get('class', '')
        web = web or kind.endswith('WebView')
        match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', el.get('bounds', ''))
        permission_control = (package in ('com.android.permissioncontroller','com.google.android.permissioncontroller')
                              and el.get('resource-id','').endswith((':id/permission_deny_button',':id/permission_message')))
        system_dialog = package == 'android' and el.get('resource-id') in ('android:id/alertTitle','android:id/aerr_close')
        if el.tag == 'node' and (package == APP or permission_control or system_dialog) and match:
            x, y, x2, y2 = map(int, match.groups())
            if x2 > x and y2 > y:
                parts = tuple(dict.fromkeys(t.strip() for t in
                    (el.get('text', ''), el.get('content-desc', ''), el.get('hint', '')) if t.strip()))
                result.append(Node(parts, kind, package, el.get('password') == 'true',
                                   el.get('enabled') != 'false', web, (x,y,x2,y2), el.get('focused') == 'true'))
        for child in el:
            walk(child, package, web)
    walk(root)
    return result


def assert_clean(nodes, secret):
    for n in nodes:
        for t in n.parts:
            if secret in t or re.search(r'dsh-auth-|(?:[?&]token=)|/Users/|/Volumes/|/home/runner/|/private/var/', t, re.I):
                raise CheckFailed('Sensitive material was visible; capture withheld.')


def checked(args, *, data=None, timeout=20):
    try:
        p = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise CheckFailed('A platform tool did not respond.') from None
    if p.returncode:
        raise CheckFailed('A platform tool returned a failure.')
    return p.stdout


def hosted(stage, device):
    if not (os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT', 'github-hosted') == 'github-hosted'
            and os.environ.get('RUNNER_OS') == 'Linux'
            and re.fullmatch(r'[0-9]+', os.environ.get('GITHUB_RUN_ID',''))
            and stage == (Path(os.environ['RUNNER_TEMP']).resolve() / 'garden-qa')
            and device == 'emulator-5554'):
        raise CheckFailed('Expected the isolated hosted Android job.')


class Phone:
    def __init__(self, stage, device):
        self.stage = stage
        self.dest = stage / 'private-maestro'
        hosted(stage, device)
        self.adb = ['adb', '-s', device]
        # Guard any reset/input against attaching to a physical or unrelated AVD.
        if self.call('emu','avd','name').splitlines()[:1] != [b'GardenQA']:
            raise CheckFailed('Expected the newly provisioned GardenQA emulator.')
        if self.call('shell','getprop','ro.boot.qemu').strip() != b'1' and self.call('shell','getprop','ro.kernel.qemu').strip() != b'1':
            raise CheckFailed('Expected an emulated Android device.')
        if b'tcp:43671 tcp:43671' not in self.call('reverse','--list'):
            raise CheckFailed('Expected the synthetic loopback port mapping.')
        self.secret = (stage/'token.private').read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9._~=-]{16,512}', self.secret):
            raise CheckFailed('Expected a file containing a single isolated launch token.')
        self.dest.mkdir(mode=0o700, exist_ok=True)
        if any(self.dest.iterdir()):
            raise CheckFailed('Expected an empty private capture directory.')
        self.path = '/data/local/tmp/garden-record-' + str(os.getpid())
        self.phase = 'chooser'
        self.last = []

    def call(self, *args, **kwargs):
        return checked([*self.adb, *map(str,args)], **kwargs)

    def shell(self, *args, **kwargs):
        return self.call('shell', *args, **kwargs).decode(errors='replace')

    def items(self):
        try:
            # exec-out has no controlling tty on some Android builds. The shell
            # writes this owner-only transient hierarchy, streams it, then removes it.
            tree = self.path + '-ui.xml'
            cmd = (f'umask 077; rm -f {tree}; uiautomator dump {tree} >/dev/null 2>&1 '
                   f'&& cat {tree}; rm -f {tree}')
            raw = self.call('exec-out', cmd, timeout=18)
            self.last_hierarchy = b'</hierarchy>' in raw
            result = parse(raw)
        except CheckFailed:
            self.last_hierarchy = False
            result = []
        self.last = result
        if self.phase == 'chooser':
            dialog=[n for n in result if n.package=='android']
            title=any(n.exact("Pixel Launcher isn't responding", "Pixel Launcher isn’t responding") for n in dialog)
            close=next((n for n in dialog if n.enabled and n.exact('Close app')),None)
            if title and close:
                self.tap(close)
                return []
        permission = [n for n in result if n.package.endswith('permissioncontroller')]
        if any(n.matches(r'(?:send you )?notifications') for n in permission):
            button = next((n for n in permission if n.exact("Don't allow", "Don’t allow", "Deny")), None)
            if button:
                self.tap(button)
                return []
        return [n for n in result if n.package == APP]

    def tap(self, n):
        x,y,x2,y2 = n.box
        self.shell('input','tap',(x+x2)//2,(y+y2)//2)

    def enter(self, value):
        cmd = 'input text ' + shlex.quote(value.replace(' ', '%s')) + '\nexit $?\n'
        self.call('shell', data=cmd.encode())

    def clear_editor(self):
        self.shell('input','keycombination','113','29')
        self.shell('input','keyevent','67')

    def reply_checkpoint(self):
        shutil.copyfile(self.dest/'garden-session.mp4', self.stage/'reply-session.mp4')
        (self.stage/'reply-verified').write_text('full-native-reply\n')

    def native_checkpoint(self):
        shutil.copyfile(self.dest/'garden-session.mp4', self.stage/'native-session.mp4')
        (self.stage/'native-verified').write_text('cold-reopened-native-history\n')

    def web_checkpoint(self, proof):
        if proof not in ('sent-second-prompt','read-native-history'):
            raise CheckFailed('Unknown workspace verification method.')
        (self.stage/'web-verification.txt').write_text(proof+'\n')

    def hide(self):
        if re.search(r'(?:mInputShown|isInputViewShown|mIsInputViewShown)=true', self.shell('dumpsys','input_method')):
            self.shell('input','keyevent','4')

    def foreground(self):
        windows=self.shell('dumpsys','window','windows',timeout=7)
        current=next((line for line in windows.splitlines() if 'mCurrentFocus=' in line),'')
        for package,name in ((APP,'conduit'),('com.android.systemui','system-ui'),
                             ('com.google.android.apps.nexuslauncher','launcher'),('com.android.launcher','launcher'),
                             ('com.google.android.permissioncontroller','permission'),('com.android.permissioncontroller','permission')):
            if package in current:return name
        return 'other' if current and 'null' not in current else 'unknown'

    def start(self, fresh=False):
        # device.sh installs the exact APK once on a wiped and dedicated GardenQA.
        # Preserve app storage across the probe, the cold-reopen check, and recovery.
        self.shell('input','keyevent','KEYCODE_WAKEUP')
        self.shell('wm','dismiss-keyguard')
        self.shell('cmd','statusbar','collapse')
        if fresh:
            for setting in ('window_animation_scale','transition_animation_scale','animator_duration_scale'):
                self.shell('settings','put','global',setting,'0')
        self.shell('am','force-stop','--user','0',APP)
        raw=self.shell('cmd','package','resolve-activity','--brief','--user','0','-a','android.intent.action.MAIN',
                       '-c','android.intent.category.LAUNCHER','-p',APP)
        component=next((v.strip() for v in raw.splitlines() if re.fullmatch(re.escape(APP)+r'/[A-Za-z_.$][A-Za-z0-9_.$]*',v.strip())),None)
        if not component:
            raise CheckFailed('The installed debug app did not resolve a launcher activity.')
        self.launch_component=component
        self.bring_forward()
        if fresh:
            self.last_recovery=time.monotonic()
            self.recoveries=0

    def bring_forward(self):
        reply=self.shell('am','start','-W','--user','0','-a','android.intent.action.MAIN','-c',
                         'android.intent.category.LAUNCHER','-n',self.launch_component,timeout=45)
        if re.search(r'(?:^Error|^Exception|^Status: (?!ok))',reply,re.I|re.M):
            raise CheckFailed('The resolved Conduit launcher did not start.')

    def recover_chooser(self, ui):
        if any(n.matches('Open WebUI|LibreChat|Hermes') for n in ui):
            self.scroll()
            return
        now=time.monotonic()
        if now-self.last_recovery<15 or self.recoveries>=2:
            return
        self.recoveries+=1
        self.last_recovery=now
        self.last_foreground=self.foreground()
        self.shell('input','keyevent','KEYCODE_WAKEUP')
        self.shell('wm','dismiss-keyguard')
        self.shell('cmd','statusbar','collapse')
        # A matched notification prompt is already handled by items(). Do not
        # interact blindly with other controls. Recover the verified launcher.
        if self.last_foreground!='permission':
            if self.last_foreground=='conduit' and self.recoveries==2:
                self.shell('am','force-stop','--user','0',APP)
            self.bring_forward()

    def scroll(self):
        sizes = re.findall(r'(\d+)x(\d+)', self.shell('wm','size'))
        if sizes:
            w,h = map(int,sizes[-1])
            self.shell('input','swipe',w//2,h*8//10,w//2,h*35//100,350)

    def shot(self, name, ui):
        assert_clean(ui, self.secret)
        frame = self.call('exec-out','screencap','-p')
        if not frame.startswith(b'\x89PNG\r\n\x1a\n') or len(frame) < 1000:
            raise CheckFailed('The emulator did not return a complete PNG.')
        (self.dest/(name+'.png')).write_bytes(frame)

    def start_record(self):
        cmd = f'echo $$ > {self.path}.pid && exec screenrecord --size 720x1600 --bit-rate 4000000 --time-limit 180 {self.path}.mp4'
        self.movie = subprocess.Popen([*self.adb,'shell',cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.record_start = time.monotonic()
        for _ in range(30):
            if self.movie.poll() is not None:
                raise CheckFailed('Screen recording could not start.')
            if self.shell(f'test -s {self.path}.pid && echo ready || true').strip() == 'ready':
                return
            time.sleep(.1)
        raise CheckFailed('Screen recording did not acquire its process ID.')

    def stop_record(self, success):
        try:
            pid = self.shell('cat', self.path+'.pid').strip()
            if not pid.isdecimal():
                raise CheckFailed('Screen recording returned an invalid process ID.')
            completed = self.movie.poll() is not None
            if not completed:
                spec = self.shell(f'cat /proc/{pid}/cmdline 2>/dev/null || true').split('\x00')
                if self.path+'.mp4' not in spec:
                    raise CheckFailed('Screen recording process ownership changed.')
                self.shell('kill','-2',pid)
                self.movie.wait(timeout=15)
            if not success:
                return
            if completed or time.monotonic()-self.record_start >= 179:
                raise CheckFailed('The recording ended before the scenario completed.')
            with tempfile.TemporaryDirectory(dir=self.stage) as folder:
                raw = str(Path(folder)/'capture.mp4')
                self.call('pull',self.path+'.mp4',raw,timeout=35)
                target = self.dest/'garden-session.mp4'
                checked(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',raw,'-map','0:v:0',
                         '-an','-map_metadata','-1','-c:v','libx264','-threads','2','-crf','23','-preset','fast',
                         '-pix_fmt','yuv420p','-movflags','+faststart',str(target)], timeout=180)
                media = json.loads(checked(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type:format=duration','-of','json',str(target)]))
                if len(media['streams']) != 1 or media['streams'][0]['codec_name'] != 'h264' or float(media['format']['duration']) < 4:
                    raise CheckFailed('The encoded recording failed its media check.')
        finally:
            if self.movie.poll() is None:
                self.movie.terminate()
                self.movie.wait(timeout=5)
            self.shell('rm','-f',self.path+'.mp4',self.path+'.pid')

    def diagnostic(self):
        # Do not reveal captured hierarchy labels, screenshots, clipboard or token.
        known = ['Open WebUI','DeepSeek Harness','Server Address','Launch token','Connect',
                 'No conversations yet','New Chat','Chats',OPEN_WORKSPACE,'Send','Continue','Send message']
        return {'phase':self.phase, 'hierarchy_complete':getattr(self,'last_hierarchy',False),
                'last_foreground':getattr(self,'last_foreground','unknown'),
                'app_nodes':sum(n.package==APP for n in self.last), 'web_nodes':sum(n.web for n in self.last),
                'secure_fields':sum(n.secure and n.edit for n in self.last),
                'known_controls':[t for t in known if any(n.exact(t) for n in self.last)]}


def wait(phone, name, predicate, timeout=45, optional=None):
    deadline=time.monotonic()+timeout
    while time.monotonic() < deadline:
        ui=phone.items()
        for n in ui:
            if n.enabled and predicate(n):
                return n,ui
        if optional:
            optional(ui)
        time.sleep(.2)
    raise CheckFailed('Expected control missing during '+name+'.')


def web_proof(p):
    def editor(n):
        return n.web and n.edit and not n.secure and n.enabled
    def scene(ui):
        welcome=next((n for n in ui if n.web and n.exact('Continue','Allow','OK')),None)
        right=next((n for n in ui if n.web and n.enabled and n.exact('Collapse right sidebar')),None)
        return welcome or right
    def dismiss(ui):
        control=scene(ui)
        if control:p.tap(control)
    def stable(label, pred, timeout, permitted=lambda ui:True):
        before=None
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            ui=p.items()
            overlay=scene(ui)
            if overlay:
                p.tap(overlay);before=None
                time.sleep(.4);continue
            node=next((n for n in ui if n.enabled and pred(n)),None)
            if node and permitted(ui):
                if before==node.box:return node
                before=node.box
            else:before=None
            time.sleep(.38)
        raise CheckFailed('Stable workspace control missing during '+label+'.')
    def draft(ui):return any(editor(n) and n.exact(WEB_QUESTION) for n in ui)
    try:
        for attempt in range(2):
            field=stable('editor selection',editor,24)
            p.tap(field)
            stable('editor focus',lambda n:editor(n) and n.focused,6)
            if attempt:p.clear_editor()
            p.enter(WEB_QUESTION)
            try:
                wait(p,'complete web draft',lambda n:editor(n) and n.exact(WEB_QUESTION),timeout=5)
                break
            except CheckFailed:
                if attempt:raise
        p.hide()
        send=stable('submission',lambda n:n.web and n.exact('Send message'),12,permitted=draft)
        p.tap(send)
        wait(p,'official web reply',lambda n:n.web and n.matches(re.escape(WEB_ANSWER)),timeout=28,optional=dismiss)
        p.hide()
        _,ui=wait(p,'official web reply preview',lambda n:n.web and n.matches(re.escape(WEB_ANSWER)))
        return 'sent-second-prompt',ui
    except CheckFailed:
        # Reopen the known server-backed session in the separate official workspace.
        # Both text fragments must be simultaneously visible in the web transcript.
        p.hide()
        def sidebar(ui):
            if scene(ui):dismiss(ui);return
            toggle=next((n for n in ui if n.web and n.enabled and n.exact('Open sidebar','Expand sidebar','Expand left sidebar','Open left sidebar')),None)
            if toggle:p.tap(toggle)
        row,_=wait(p,'official server history row',lambda n:n.web and n.exact('Seedling notes'),timeout=28,optional=sidebar)
        p.tap(row)
        until=time.monotonic()+24
        while time.monotonic()<until:
            ui=p.items()
            overlay=scene(ui)
            if overlay:p.tap(overlay);continue
            if all(any(n.web and not n.edit and n.matches(re.escape(t)) for n in ui) for t in (QUESTION,ANSWER)):
                return 'read-native-history',ui
            left=next((n for n in ui if n.web and n.enabled and n.exact('Close sidebar','Collapse sidebar','Collapse left sidebar')),None)
            if left:p.tap(left)
            time.sleep(.35)
        raise CheckFailed('The official workspace could not verify the original question and reply.')


def flow(p):
    def act(name, fn, **kwargs):
        n,_=wait(p,name,fn,**kwargs)
        p.tap(n)
    def native(n):return not n.web
    def back():
        act('native history navigation',lambda n:native(n) and n.exact('Chats','Back to chats','Back to sessions'))
        wait(p,'native catalog',lambda n:native(n) and n.exact(OPEN_WORKSPACE))
    def reopen():
        act('synthetic session selection',lambda n:native(n) and n.matches(r'Seedling notes|Give me a checklist|Label each pot'))
        wait(p,'reopened native question',lambda n:native(n) and n.matches(re.escape(QUESTION)))
        for fragment in ('Label each pot.','Water the seedlings.',ANSWER):
            wait(p,'reopened native reply',lambda n:native(n) and n.matches(re.escape(fragment)))
    def pick_workspace(ui):
        choice=next((n for n in ui if n.enabled and native(n) and n.exact('Seedling notebook')),None)
        if choice:p.tap(choice)
    p.start(fresh=True)
    chooser,ui=wait(p,'backend selection',lambda n:native(n) and n.matches('DeepSeek Harness'),
                    timeout=100,optional=p.recover_chooser)
    p.shot('chooser',ui);p.tap(chooser)
    p.phase='empty pairing form'
    address,ui=wait(p,'pairing address',lambda n:native(n) and n.edit and not n.secure)
    if not any(n.edit and n.secure and native(n) for n in ui):
        raise CheckFailed('Expected a concealed pairing field in the fresh empty form.')
    p.shot('pairing',ui);p.tap(address);p.enter(ORIGIN)
    p.phase='concealed pairing'
    act('concealed credential entry',lambda n:native(n) and n.edit and n.secure)
    p.enter(p.secret);p.hide();act('pair request',lambda n:native(n) and n.exact('Connect','Connect to server'))
    _,ui=wait(p,'authenticated empty catalog',lambda n:native(n) and n.exact('No conversations yet'),timeout=55)
    if any(n.secure or n.matches('Launch token') for n in ui) or not any(n.exact(OPEN_WORKSPACE) for n in ui):
        raise CheckFailed('Pairing has not left the screen.')
    p.shot('paired-proof',ui)
    p.phase='native chat'
    p.start_record()
    success=False
    try:
        act('create native chat',lambda n:native(n) and n.exact('New Chat'))
        act('native composer',lambda n:native(n) and n.edit and not n.secure,
            optional=pick_workspace)
        p.enter(QUESTION);act('native send',lambda n:native(n) and n.exact('Send'))
        wait(p,'native streamed reply',lambda n:native(n) and n.matches(re.escape(ANSWER)))
        p.hide()
        _,ui=wait(p,'native transcript preview',lambda n:native(n) and n.matches(re.escape(ANSWER)))
        for fragment in (QUESTION,'Label each pot.','Water the seedlings.',ANSWER):
            wait(p,'complete native turn',lambda n:native(n) and n.matches(re.escape(fragment)))
        p.shot('transcript',ui)
        success=True
    finally:
        p.stop_record(success)
    p.reply_checkpoint()
    p.phase='reopened native session'
    back();reopen();back()
    p.phase='cold launch'
    p.start()
    wait(p,'restored paired catalog',lambda n:native(n) and n.exact(OPEN_WORKSPACE))
    reopen()
    p.native_checkpoint()
    print('Verified Android native video, reopened checklist and cold launch.',flush=True)
    back()
    p.phase='official web workspace'
    act('workspace entry',lambda n:native(n) and n.exact(OPEN_WORKSPACE))
    proof,ui=web_proof(p)
    p.shot('workspace',ui)
    act('exit embedded workspace',lambda n:native(n) and n.exact('Back','Chats','Back to chats','Back to Conduit'))
    wait(p,'catalog after workspace verification',lambda n:native(n) and n.exact(OPEN_WORKSPACE))
    p.web_checkpoint(proof)
    print('Verified Android official workspace ('+proof+').',flush=True)


def main():
    os.umask(0o077)
    p=None
    try:
        p=Phone(Path(os.environ['QA_STAGE']).resolve(),os.environ['QA_DEVICE'])
        flow(p)
    except Exception as e:
        if p:
            print('Android diagnostic '+json.dumps(p.diagnostic()),file=sys.stderr)
        print(str(e) if isinstance(e,CheckFailed) else 'Android capture stopped ('+type(e).__name__+').',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
