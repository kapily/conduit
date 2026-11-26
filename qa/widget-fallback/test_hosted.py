import http.client
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import hosted


class Controls(unittest.TestCase):
    cap='made-up-run-capability'
    token='manufactured-test-value'

    def setUp(self):
        self.media=mock.Mock()
        self.state=hosted.Receipt(self.token,self.cap,self.media)
        self.server=hosted.serve(self.state,0)
        self.endpoint=self.server.server_address

    def tearDown(self):
        self.server.shutdown();self.server.server_close()

    def request(self,action,**changes):
        conn=http.client.HTTPConnection(*self.endpoint,timeout=3)
        headers={'Host':f'127.0.0.1:{hosted.PORT}','X-Garden-Run':self.cap,'Content-Length':'0'}
        headers.update(changes)
        conn.request('POST','/'+action,body=b'',headers=headers)
        resp=conn.getresponse();out=(resp.status,resp.read(),resp.getheader('Cache-Control'));conn.close()
        return out

    def test_post_pair_recording_and_verification_in_order(self):
        for action in ('paired','reply','history'):
            self.assertEqual(self.request(action)[0],409)
        self.assertEqual(self.media.mock_calls,[])
        self.assertEqual(self.request('credential'),(200,self.token.encode(),'no-store'))
        self.assertEqual(self.request('credential')[0],409)
        self.assertEqual(self.media.mock_calls,[])
        self.assertEqual(self.request('paired')[0],200)
        self.assertEqual(self.request('history')[0],409)
        self.assertEqual(self.request('reply')[0],200)
        self.assertEqual(self.request('history')[0],200)
        self.assertEqual(self.media.mock_calls,[mock.call.paired(),mock.call.reply(),mock.call.history()])
        self.assertEqual(self.request('paired')[0],409)

    def test_loopback_origin_sender_and_per_run_capability(self):
        for headers in ({'Host':'localhost:43673'},{'Host':'127.0.0.1:80'},{'Origin':'http://127.0.0.1:43671'},
                        {'X-Garden-Run':'incorrect'},{'Content-Length':'1'}):
            self.assertEqual(self.request('credential',**headers)[0],403)
        self.assertEqual(self.state.request('10.0.2.2',f'127.0.0.1:{hosted.PORT}',self.cap,None,'/credential','0'),(403,b''))
        self.assertEqual(self.state.phase,'initial')

    def test_failed_encoder_cannot_mark_transcript_verified(self):
        self.request('credential');self.request('paired')
        self.media.reply.side_effect=RuntimeError('synthetic encoding failure')
        self.assertEqual(self.request('reply')[0],500)
        self.assertEqual(self.request('history')[0],409)
        self.assertEqual(self.state.phase,'paired')

    def test_preparation_is_scoped_to_disposable_test_target(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            c=Path(d);(c/'pubspec.yaml').write_text('name: example\ndev_dependencies:\n  flutter_test:\n    sdk: flutter\n')
            hosted.prepare(c,Path(__file__).parent)
            self.assertTrue((c/'integration_test/garden_device_test.dart').is_file())
            self.assertEqual((c/'pubspec.yaml').read_text().count('integration_test:'),1)
            with self.assertRaises(hosted.Failure):hosted.prepare(c,Path(__file__).parent)

    def test_concurrent_credential_requests_only_one_succeeds(self):
        results=[]
        threads=[threading.Thread(target=lambda:results.append(self.request('credential')[0])) for _ in range(3)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results),[200,409,409])


if __name__=='__main__':unittest.main()
