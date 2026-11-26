"""Static OpenAI-compatible model used solely by the isolated Harness demo."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time

MODEL = "field-notes"
ANSWERS = {
    "default": "1. Label each pot.\n2. Water the seedlings.\n3. Check the soil again tomorrow.",
    "checklist": "1. Label each pot.\n2. Water the seedlings.\n3. Check the soil again tomorrow.",
    "title": "Seedling notes",
    "ping": "PONG",
}

class Endpoint(BaseHTTPRequestHandler):
    def log_message(self, *unused):
        pass

    def do_GET(self):
        if self.path == '/v1/models':
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"object":"list","data":[{"id":MODEL,"object":"model","owned_by":"local-fixture"}]}).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_error(404); return
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if body.get('model') != MODEL:
            self.send_error(400, 'Unknown demo model'); return
        prompts=[m.get('content','') for m in body.get('messages',[]) if m.get('role')=='user']
        text=json.dumps(prompts[-1:]).lower()
        system=' '.join(str(m.get('content','')) for m in body.get('messages',[]) if m.get('role')=='system').lower()
        needs_title='title' in text or (not body.get('tools') and ('title' in system or 'summar' in system))
        if needs_title:
            answer='Arugula tray' if 'arugula' in text or 'arugula' in system else ANSWERS['title']
        elif 'arugula' in text:
            answer='Put the copper marker in the arugula tray.'
        elif 'ping' in text:
            answer=ANSWERS['ping']
        else:
            answer=ANSWERS['checklist']
        self.send_response(200)
        stream=bool(body.get('stream'))
        self.send_header('Content-Type','text/event-stream' if stream else 'application/json')
        self.send_header('Cache-Control','no-cache')
        self.end_headers()
        root={'id':'synthetic-completion','object':'chat.completion.chunk' if stream else 'chat.completion','created':int(time.time()),'model':MODEL}
        if not stream:
            self.wfile.write(json.dumps(dict(root,choices=[{'index':0,'message':{'role':'assistant','content':answer},'finish_reason':'stop'}],usage={'prompt_tokens':12,'completion_tokens':24,'total_tokens':36})).encode())
            return
        try:
            def emit(delta,finish=None):
                chunk=dict(root,choices=[{'index':0,'delta':delta,'finish_reason':finish}])
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode());self.wfile.flush()
            emit({'role':'assistant','content':''})
            for i in range(0,len(answer),8):
                emit({'content':answer[i:i+8]});time.sleep(0.19)
            emit({},'stop')
            self.wfile.write(b'data: [DONE]\n\n')
        except (BrokenPipeError,ConnectionResetError):
            pass

ThreadingHTTPServer(('127.0.0.1',43672),Endpoint).serve_forever()
