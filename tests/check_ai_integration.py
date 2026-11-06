"""Real LiteLLM -> local HTTP fixture -> application persistence, no vendor keys/costs.
Run --serve to inspect the same isolated app in a browser after the checks.
"""
import argparse
import asyncio
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import project_env
import app
import app_settings
import ai_service as ai
import library
import quiz
import httpx
import uvicorn

FIXTURE={'version':1,'title':'接口联调示例 · 主动回忆','description':'本地模拟供应商响应，仅用于验证接口和学习流程，不代表真实模型出题效果。','questions':[
    {'id':'q1','type':'true_false','prompt':'主动回忆是在不看答案的情况下，从记忆中提取知识。','answer':True,'points':1,'explanation':'闭卷提取知识是本课介绍的主动回忆。'},
    {'id':'q2','type':'fill_blank','prompt':'写出本课示例的三个复习日期（数字，用分号分隔）。','points':1,'blanks':[{'label':'复习日期','answer':{'mode':'all_of','values':['1','3','7'],'case_sensitive':False}}],'explanation':'第1、3、7天分别复习，体现间隔重复。'}]}


class Provider(BaseHTTPRequestHandler):
    receipts=[]
    def log_message(self,*args):pass
    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']))
        if self.path=='/v1/audio/transcriptions':
            assert b'RIFF' in body and b'WAVE' in body and b'lesson-segment.wav' in body
            self.receipts.append('speech')
            data=json.dumps({'text':'本地接口语音联调。Keep the original English.'}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        if self.path!='/v1/chat/completions':self.send_error(404);return
        request=json.loads(body)
        assert isinstance(request['messages'],list)
        vision=any(isinstance(m.get('content'),list) and any(p.get('type')=='image_url' for p in m['content']) for m in request['messages'])
        text='\n'.join(m.get('content','') for m in request['messages'] if isinstance(m.get('content',''),str))
        if vision:result='He go.\n课堂例句，保留原文。';kind='vision'
        elif '连接测试' in text:result='OK';kind='test'
        elif '学习评估教练' in text:result='【本地接口联调示例】第一题理解正确，第二题需复习第 1、3、7 天的示例。建议回看课程后闭卷复述。';kind='analysis'
        else:result=json.dumps(FIXTURE,ensure_ascii=False);kind='generate'
        self.receipts.append(kind)
        data=json.dumps({'id':'chatcmpl-local-fixture','object':'chat.completion','created':int(time.time()),'model':request['model'],'choices':[{'index':0,'message':{'role':'assistant','content':result},'finish_reason':'stop'}],'usage':{'prompt_tokens':12,'completion_tokens':20,'total_tokens':32}},ensure_ascii=False).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


def only_local(event,args):
    if event=='socket.connect' and isinstance(args[1],tuple) and args[1][0] not in {'127.0.0.1','::1'}:
        raise OSError('Integration test forbids non-loopback traffic')
    if event=='socket.getaddrinfo' and args[0] not in {'127.0.0.1','localhost','::1',None}:
        raise OSError('Integration test forbids external DNS')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    sys.addaudithook(only_local)
    with tempfile.TemporaryDirectory(prefix='ai-integration-',dir=project_env.DATA/'tmp') as folder:
        root=Path(folder);app.JOBS=root/'jobs';app.JOBS.mkdir();quiz.QUIZZES=root/'quizzes';library.DB=root/'library.sqlite3';library.COVERS=root/'covers';ai.SETTINGS=root/'ai'/'providers.json';app_settings.PATH=root/'app-settings.json';ai.TASKS_DIR=root/'ai'/'tasks'
        job=app.new_job('transcribe','接口联调课程.wav','base','zh');job.update(status='completed',edited_text='主动回忆是不看答案时提取知识。间隔重复可以在学习后第1、3、7天复习。',media_file='source.wav',size=3200)
        app.write_job(job)
        with wave.open(str(app.job_dir(job['id'])/'source.wav'),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\x00'*3200)
        provider=ThreadingHTTPServer(('127.0.0.1',18999),Provider);threading.Thread(target=provider.serve_forever,daemon=True).start()
        config=uvicorn.Config(app.app,host='127.0.0.1',port=8766,log_level='error',access_log=False);server=uvicorn.Server(config);server_loop=[]
        async def serve():
            server_loop.append(asyncio.get_running_loop());await server.serve()
        thread=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);thread.start()
        try:
            with httpx.Client(base_url='http://127.0.0.1:8766',timeout=30,trust_env=False) as client:
                deadline=time.monotonic()+10
                while not server.started and time.monotonic()<deadline:time.sleep(.02)
                def request(method,path,**kwargs):
                    response=client.request(method,path,**kwargs);response.raise_for_status();return response.json()
                profile=request('POST','/api/ai/profiles',json={'name':'本地接口联调（非真实供应商）','provider':'openai','model':'local-fixture','capabilities':['chat','transcription','vision'],'base_url':'http://127.0.0.1:18999/v1','max_tokens':8192,'timeout':60})
                def settle(task):
                    until=time.monotonic()+30
                    while time.monotonic()<until:
                        current=request('GET','/api/ai/tasks/'+task['id'])
                        if current['status'] not in {'queued','running'}:
                            assert current['status']=='completed',current;return current
                        time.sleep(.03)
                    raise AssertionError('Provider did not finish')
                settle(request('POST','/api/ai/test',json={'profile_id':profile['id']}))
                generated=settle(request('POST','/api/ai/courses/'+job['id']+'/generate',json={'profile_id':profile['id'],'count':2}))
                record=request('GET','/api/quizzes/'+generated['result_id']);assert 'answer' not in record['quiz']['questions'][0]
                record=request('POST','/api/quizzes/'+record['id']+'/submit',json={'revision':record['revision'],'answers':{'q1':True,'q2':['1;3;5']}})
                analysed=settle(request('POST','/api/ai/quizzes/'+record['id']+'/analyze',json={'profile_id':profile['id'],'revision':record['revision']}))
                record=request('GET','/api/quizzes/'+record['id']);assert record['analysis']['origin']=='provider' and '接口联调' in record['analysis']['text']
                assert Provider.receipts==['test','generate','analysis'],Provider.receipts
                # Route speech and review through the actual queued worker, without native ASR downloads.
                settings=request('GET','/api/settings')['settings']
                models={key:'provider:'+profile['id'] for key in settings['models']}
                request('PATCH','/api/settings',json={**{key:settings[key] for key in ('revision','review_enabled','ocr_enabled','review_policy','trust')},'models':models,'ocr_enabled':False,'review_policy':'by_language'})
                with (project_env.ROOT/'tests/assets/jfk.flac').open('rb') as audio:
                    spoken=request('POST','/api/jobs',data={'language':'en'},files={'file':('jfk.flac',audio,'audio/flac')})
                assert spoken['uses_local_compute'] is False and spoken['speed_profile'] is None
                until=time.monotonic()+40
                while time.monotonic()<until:
                    spoken=request('GET','/api/jobs/'+spoken['id'])
                    if spoken['status'] in {'completed','failed','cancelled'}:break
                    time.sleep(.1)
                assert spoken['status']=='completed',spoken
                assert spoken['segments'] and spoken['segments'][0]['selected_source']=='review',spoken
                assert spoken['engine_metrics']==[],spoken
                from remote_inference import RemoteSession
                visual=asyncio.run_coroutine_threadsafe(RemoteSession().vision_call(project_env.ROOT/'tests/assets/mixed-ocr.png',{'profile_id':profile['id']},{'prompt_snapshot':{'vision':'识别课程文字，保留原文'}}),server_loop[0]).result(30)
                assert 'He go.' in visual[0]['text'],visual
                assert Provider.receipts==['test','generate','analysis','speech','speech','vision'],Provider.receipts
                for capability in ['transcription','vision']:
                    dedicated=request('POST','/api/ai/profiles',json={'name':'用途测试 '+capability,'provider':'openai','model':'local-fixture','base_url':'http://127.0.0.1:18999/v1','capabilities':[capability]})
                    settle(request('POST','/api/ai/test',json={'profile_id':dedicated['id']}))
                assert Provider.receipts==['test','generate','analysis','speech','speech','vision','speech','vision'],Provider.receipts
                print('PASS: real LiteLLM SDK made 8 loopback HTTP calls; generation, grading, analysis, queued audio transcription + review, vision, per-language adoption and cloud speed bypass verified. No external traffic or native ASR loaded.',flush=True)
                if args.serve:
                    print('Isolated browser test app: http://127.0.0.1:8766/ (Ctrl+C removes this test workspace)',flush=True)
                    while True:time.sleep(.5)
        except KeyboardInterrupt:pass
        finally:
            server.should_exit=True;thread.join(timeout=15);provider.shutdown();provider.server_close()


if __name__=='__main__':main()
