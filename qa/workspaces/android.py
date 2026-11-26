"""Test only a new GardenQA guest and save post-authentication captures."""
import importlib.util
import os
from pathlib import Path
import re
import sys
import time
spec=importlib.util.spec_from_file_location('installed_android',Path(__file__).resolve().parents[1]/'android-native-driver.py')
d=importlib.util.module_from_spec(spec);sys.modules[spec.name]=d;spec.loader.exec_module(d)

class WorkspacePhone(d.Phone):
    def bring_forward(self):
        # The app may already be initializing while a freshly booted host times
        # out waiting for an Android launch measurement. Read the real chooser
        # or restored workspace through accessibility to verify readiness.
        reply=self.shell('am','start','--user','0','-a','android.intent.action.MAIN',
                         '-c','android.intent.category.LAUNCHER','-n',self.launch_component,timeout=35)
        if re.search(r'^Error|^Exception',reply,re.I|re.M):
            # Some images pick a disabled alias on their first package scan.
            # Ask Android's launcher to activate only the pinned app and wait
            # for its own actual UI in the existing accessibility loop.
            self.shell('monkey','-p',d.APP,'-c','android.intent.category.LAUNCHER','1',timeout=30)


def main():
    os.umask(0o077)
    stage=Path(os.environ['QA_STAGE']).resolve()
    phone=WorkspacePhone(stage,os.environ['QA_DEVICE'])
    phone.dest=stage/'workspace-evidence';phone.dest.mkdir(mode=0o700)
    def matches(s):
        return lambda n:not n.web and n.matches(r'(?:^|\n)'+re.escape(s)+r'(?:$|\n)')
    def wait(s,**kw):return d.wait(phone,phone.phase+' / '+s,matches(s),**kw)
    def tap(s):
        n,_=wait(s);phone.tap(n)
    def folder_open():
        for s in ('All chats','Lunar maps','Meteor watch'):
            for n in phone.items():
                if not n.web and (n.exact(s) or (n.matches(re.escape(s)) and n.matches('Workspaces'))):
                    phone.tap(n)
                    wait('New workspace')
                    return
        raise d.CheckFailed('The folder selector was missing.')
    def assert_hidden(ui,s):
        if any(not n.web and n.matches(re.escape(s)) for n in ui):raise d.CheckFailed('The filtered catalog included a different workspace.')
    authenticated=False
    recording=False
    try:
        phone.start(fresh=True)
        c,_=d.wait(phone,'backend chooser',lambda n:not n.web and n.matches('DeepSeek Harness'),timeout=100,optional=phone.recover_chooser)
        phone.tap(c);phone.phase='pairing'
        address,ui=d.wait(phone,'pairing address',lambda n:not n.web and n.edit and not n.secure)
        if not any(n.edit and n.secure for n in ui):raise d.CheckFailed('Expected a concealed field.')
        phone.tap(address);phone.enter(d.ORIGIN)
        token,_=d.wait(phone,'concealed input',lambda n:n.edit and n.secure)
        phone.tap(token);phone.enter(phone.secret);phone.hide();tap('Connect')
        wait('Blue hour observing plan',timeout=55);_,clean=wait('Three crater sketches');d.assert_clean(clean,phone.secret)
        if any(n.edit or n.secure for n in clean):raise d.CheckFailed('Pairing input remained on screen.')
        authenticated=True
        phone.start_record();recording=True
        phone.phase='switch workspace';folder_open();tap('Lunar maps')
        _,ui=wait('Three crater sketches');assert_hidden(ui,'Blue hour observing plan')
        phone.shot('selected',ui)
        folder_open();_,ui=wait('New workspace');phone.shot('workspaces',ui)
        (phone.dest/'switch-verified').write_text(os.environ['QA_COMMIT'])
        tap('New workspace');phone.phase='create workspace'
        tap('New Folder')
        field,_=d.wait(phone,'new folder name',lambda n:n.edit and not n.secure)
        phone.tap(field);phone.enter('Meteor watch');phone.hide();tap('Create')
        wait('No subfolders');tap('Use this folder')
        _,ui=wait('No conversations yet');assert_hidden(ui,'Three crater sketches')
        assert any(matches('Meteor watch')(n) for n in ui)
        tap('New Chat')
        field,_=d.wait(phone,'new workspace composer',lambda n:n.edit and not n.secure)
        tap('Chats');wait('Meteor watch');folder_open()
        _,ui=wait('Meteor watch');phone.shot('created',ui)
        phone.stop_record(True);recording=False
        (phone.dest/'garden-session.mp4').rename(phone.dest/'workspaces.mp4')
        phone.phase='cold launch'
        phone.start()
        _,ui=wait('Meteor watch',timeout=70)
        assert_hidden(ui,'Blue hour observing plan');assert_hidden(ui,'Three crater sketches')
        physics=Path((stage/'workspace-home').read_text());assert physics==Path('/tmp/sky-notebooks') and (physics/'Meteor watch').is_dir()
        (phone.dest/'verified').write_text(os.environ['QA_COMMIT'])
        print('Verified switching, folder creation, registration and a new chat on the actual Android app.')
    except Exception as error:
        if recording:
            try:phone.stop_record(False)
            except Exception:pass
        if authenticated:
            try: phone.shot('diagnostic',phone.items())
            except Exception: pass
        print('Workspace device check stopped during '+phone.phase+'.',flush=True)
        raise SystemExit(str(error) if isinstance(error,d.CheckFailed) else type(error).__name__)
if __name__=='__main__':main()
