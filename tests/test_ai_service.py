import asyncio
import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import ai_service as ai
import library
import quiz
from fastapi.testclient import TestClient


class AIServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=app.project_env.DATA/'tmp');self.root=Path(self.tmp.name)
        (self.root/'jobs').mkdir()
        self.patches=[patch.object(ai,'SETTINGS',self.root/'ai'/'providers.json'),patch.object(ai,'TASKS_DIR',self.root/'ai'/'tasks'),
                      patch.object(quiz,'QUIZZES',self.root/'quizzes'),patch.object(library,'DB',self.root/'library.sqlite3'),patch.object(app,'JOBS',self.root/'jobs')]
        for p in self.patches:p.start()
        self.client=TestClient(app.app);self.client.__enter__()
        self.secret='unit-test-secret-only'
        self.config={'name':'测试模型','provider':'openai','model':'local-test','base_url':'http://127.0.0.1:18999/v1','api_key':self.secret,'provider_params':{'api_version':'test'}}
        self.connection=self.client.post('/api/ai/profiles',json=self.config).json()
        self.job=app.new_job('transcribe','lesson.mp4','base','zh');self.job.update(status='completed',edited_text='课程讲解主动回忆与间隔重复。')
        app.write_job(self.job)

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def response(self,text=None,finish='stop'):
        return {'text':json.dumps(quiz.example(),ensure_ascii=False) if text is None else text,'finish_reason':finish,'usage':{'prompt_tokens':15,'completion_tokens':25,'total_tokens':40}}

    def wait(self,identity):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            response=self.client.get('/api/ai/tasks/'+identity)
            task=response.json()
            if task['status'] not in {'queued','running'}:return task
            time.sleep(.01)
        self.fail('AI task did not settle')

    def generate(self,**kwargs):
        response=self.client.post('/api/ai/courses/'+self.job['id']+'/generate',json={'profile_id':self.connection['id'],**kwargs})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def record(self):
        r=self.client.post('/api/quizzes',json={'content':json.dumps(quiz.example()),'source_job_id':self.job['id']}).json()
        return self.client.post('/api/quizzes/'+r['id']+'/submit',json={'revision':r['revision'],'answers':{'q1':'A','q5':'我会闭卷回忆'}}).json()

    def test_registry_and_secrets_stay_out_of_responses(self):
        result=self.client.get('/api/ai/settings')
        self.assertGreater(len(result.json()['providers']),100)
        self.assertNotIn(self.secret,result.text);self.assertNotIn('api_version',result.text)
        self.assertTrue(self.connection['has_key']);self.assertTrue(self.connection['has_provider_params'])
        self.assertEqual(stat.S_IMODE(ai.SETTINGS.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(ai.SETTINGS.parent.stat().st_mode),0o700)
        self.assertEqual(ai.profile(self.connection['id'])['api_key'],self.secret)
        catalog=self.client.get('/api/ai/catalog?provider=deepseek')
        self.assertEqual(catalog.status_code,200)

    def test_edit_key_preservation_and_destination_change(self):
        base='/api/ai/profiles/'+self.connection['id']
        body={k:v for k,v in self.config.items() if k not in {'api_key','provider_params'}}
        self.assertEqual(self.client.patch(base,json={**body,'name':'重命名'}).status_code,200)
        self.assertEqual(ai.profile(self.connection['id'])['api_key'],self.secret)
        self.assertEqual(self.client.patch(base,json={**body,'base_url':'https://other.example/v1'}).status_code,422)
        self.assertEqual(self.client.patch(base,json={**body,'clear_key':True,'clear_provider_params':True}).status_code,200)
        stored=ai.profile(self.connection['id']);self.assertEqual(stored['api_key'],'');self.assertEqual(stored['provider_params'],{})

    def test_bad_config_cross_site_and_protected_params(self):
        for changes in [{'name':'  '},{'provider':'not-real'},{'base_url':'http://remote.example/v1'},
                        {'base_url':'https://user:password@example.com/v1'},{'base_url':'https://example.com?key=abc'},
                        {'provider_params':{'callbacks':['bad']}},{'provider_params':{'messages':[]}}]:
            result=self.client.post('/api/ai/profiles',json={**self.config,**changes})
            self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(self.client.post('/api/ai/profiles',json=self.config,headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/ai/tasks/not-a-uuid').status_code,404)

    def test_generation_reuses_schema_hides_answers_and_records_source(self):
        with patch.object(ai,'model_call',new=AsyncMock(return_value=self.response())) as call:
            task=self.wait(self.generate(count=5)['id'])
        self.assertEqual(task['status'],'completed',task)
        record=self.client.get('/api/quizzes/'+task['result_id']).json()
        self.assertEqual(record['source_job_id'],self.job['id'])
        self.assertNotIn('answer',record['quiz']['questions'][0])
        self.assertEqual(record['origin']['model'],'local-test')
        self.assertIn('主动回忆',call.call_args.args[1][1]['content'])
        self.assertNotIn(self.secret,call.call_args.args[1][1]['content'])
        self.assertNotIn(self.secret,json.dumps(task))
        self.assertNotIn('raw_output',task)
        self.assertEqual(task['usage']['total_tokens'],40)
        self.assertIn('version',self.client.get('/api/ai/tasks/'+task['id']+'/output').text)

    def test_invalid_or_truncated_output_never_creates_half_quiz(self):
        for response in [self.response('不是JSON'),self.response(finish='length'),self.response('')]:
            with patch.object(ai,'model_call',new=AsyncMock(return_value=response)):
                task=self.wait(self.generate()['id'])
            self.assertEqual(task['status'],'failed',task)
            self.assertEqual(self.client.get('/api/quizzes').json(),[])

    def test_errors_are_sanitized_no_automatic_retry(self):
        class AuthenticationError(Exception):pass
        with patch.object(ai,'model_call',new=AsyncMock(side_effect=AuthenticationError('Bearer '+self.secret))) as call:
            task=self.wait(self.generate()['id'])
        self.assertEqual(task['status'],'failed');self.assertIn('认证失败',task['message']);self.assertNotIn(self.secret,json.dumps(task));self.assertEqual(call.await_count,1)

    def test_duplicate_request_and_cancel(self):
        async def slow(*args,**kwargs):
            await asyncio.sleep(10)
            return self.response()
        with patch.object(ai,'model_call',new=slow):
            first=self.generate();second=self.generate();self.assertEqual(first['id'],second['id'])
            self.assertEqual(self.client.delete('/api/ai/profiles/'+self.connection['id']).status_code,409)
            task=self.client.post('/api/ai/tasks/'+first['id']+'/cancel').json()
            self.assertEqual(task['status'],'cancelled')
        self.assertEqual(self.client.get('/api/quizzes').json(),[])

    def test_analysis_keeps_old_feedback_and_detects_revision_conflict(self):
        record=self.record();base='/api/quizzes/'+record['id']
        record=self.client.patch(base+'/analysis',json={'revision':record['revision'],'text':'旧复习笔记'}).json()
        with patch.object(ai,'model_call',new=AsyncMock(return_value=self.response('逐题评价与新的复习计划'))):
            task=self.wait(self.client.post('/api/ai/quizzes/'+record['id']+'/analyze',json={'revision':record['revision'],'profile_id':self.connection['id']}).json()['id'])
        self.assertEqual(task['status'],'completed',task)
        saved=self.client.get(base).json();self.assertEqual(saved['analysis']['origin'],'provider');self.assertNotIn('analysis_history',saved)
        full=self.client.get(base+'/export/json').json();self.assertEqual(full['analysis_history'][0]['text'],'旧复习笔记')
        async def slow(*args,**kwargs):
            await asyncio.sleep(.15)
            return self.response('旧答案的模型分析')
        with patch.object(ai,'model_call',new=slow):
            task=self.client.post('/api/ai/quizzes/'+record['id']+'/analyze',json={'revision':saved['revision'],'profile_id':self.connection['id']}).json()
            self.client.patch(base+'/answers',json={'revision':saved['revision'],'answers':{'q1':'B'}})
            task=self.wait(task['id'])
        self.assertEqual(task['status'],'conflict')
        self.assertEqual(self.client.get(base).json()['analysis']['text'],'逐题评价与新的复习计划')
        self.assertIn('旧答案',self.client.get('/api/ai/tasks/'+task['id']+'/output').text)

    def test_different_quizzes_in_same_course_are_distinct_requests(self):
        first=self.record();second=self.record()
        async def slow(*args,**kwargs):
            await asyncio.sleep(.12)
            return self.response('分别评价这份作答')
        with patch.object(ai,'model_call',new=AsyncMock(side_effect=slow)) as call:
            tasks=[self.client.post('/api/ai/quizzes/'+record['id']+'/analyze',json={'revision':record['revision'],'profile_id':self.connection['id']}).json() for record in (first,second)]
            self.assertNotEqual(tasks[0]['id'],tasks[1]['id'])
            for task in tasks:self.assertEqual(self.wait(task['id'])['status'],'completed')
        self.assertEqual(call.await_count,2)

    def test_extra_headers_are_private_and_validated(self):
        body={**self.config,'provider_params':{'extra_headers':{'X-Test-Header':'header-secret'}}}
        response=self.client.post('/api/ai/profiles',json=body)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('header-secret',response.text)
        self.assertEqual(ai.profile(response.json()['id'])['provider_params']['extra_headers']['X-Test-Header'],'header-secret')
        body['provider_params']['extra_headers']={'Header':'bad\nvalue'}
        self.assertEqual(self.client.post('/api/ai/profiles',json=body).status_code,422)

    def test_analysis_requires_submission_and_restart_never_replays(self):
        record=self.client.post('/api/quizzes',json={'content':json.dumps(quiz.example())}).json()
        self.assertEqual(self.client.post('/api/ai/quizzes/'+record['id']+'/analyze',json={'revision':0,'profile_id':self.connection['id']}).status_code,409)
        task=ai.prepared_task('test',ai.profile(self.connection['id']));ai.save_task(task)
        asyncio.run(ai.startup());stored=ai.load_task(task['id'])
        self.assertEqual(stored['status'],'failed');self.assertIn('没有自动重试',stored['message'])


if __name__=='__main__':unittest.main()
