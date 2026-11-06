"""Saved defaults, model routing, prompt exports and review source policy."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import app_settings as settings
import ai_service as ai
import quiz
import library
from exports import study_prompt,review_material
from pipeline import apply_review_policy


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=app.project_env.DATA/'tmp');self.root=Path(self.temp.name)
        self.patches=[patch.object(settings,'PATH',self.root/'defaults.json'),patch.object(ai,'SETTINGS',self.root/'providers.json'),patch.object(app,'JOBS',self.root/'jobs'),patch.object(library,'DB',self.root/'library.sqlite3'),patch.object(quiz,'QUIZZES',self.root/'quizzes')]
        (self.root/'jobs').mkdir()
        for item in self.patches:item.start()
        self.client=TestClient(app.app)
    def tearDown(self):
        self.client.close()
        for item in self.patches[::-1]:item.stop()
        self.temp.cleanup()
    def profile(self,capabilities):
        result=self.client.post('/api/ai/profiles',json={'name':'测试','provider':'openai','model':'test-model','base_url':'http://127.0.0.1:18999/v1','api_key':'secret-test-only','capabilities':capabilities})
        self.assertEqual(result.status_code,200,result.text);return 'provider:'+result.json()['id']
    def edit(self,**values):
        config=settings.read();body={k:config[k] for k in ('revision','review_enabled','ocr_enabled','review_policy','trust','models')};body.update(values)
        result=self.client.patch('/api/settings',json=body);self.assertEqual(result.status_code,200,result.text);return result.json()['settings']
    def upload(self,**data):
        with patch.object(app.TASKS,'put'):
            result=self.client.post('/api/jobs',data=data,files={'file':('lesson.mp4',b'fixture')})
        self.assertEqual(result.status_code,200,result.text);return result.json()
    def test_defaults_flags_policy_persist_and_override_per_job(self):
        self.edit(review_enabled=False,ocr_enabled=False,review_policy='by_language')
        saved=json.loads(settings.PATH.read_text());self.assertFalse(saved['review_enabled']);self.assertEqual(settings.PATH.stat().st_mode&0o777,0o600)
        job=self.upload();self.assertFalse(job['review_enabled']);self.assertFalse(job['ocr_enabled']);self.assertTrue(job['uses_local_compute'])
        overridden=self.upload(review_enabled='true');self.assertTrue(overridden['review_enabled']);self.assertEqual(overridden['review_binding']['model'],'whisper-large-v3-mlx')
    def test_independent_routing_cloud_no_local_speed_no_secrets(self):
        audio=self.profile(['transcription']);vision=self.profile(['vision']);chat=self.profile(['chat'])
        models={'speech':audio,'review':audio,'vision':vision,'generate':chat,'analysis':'none'}
        self.edit(models=models)
        job=self.upload();self.assertIsNone(job['speed_profile']);self.assertFalse(job['uses_local_compute']);self.assertEqual(job['speech_binding']['value'],audio)
        self.assertNotIn('secret-test-only',json.dumps(job));self.assertNotIn('secret-test-only',self.client.get('/api/settings').text)
        override=self.upload(model='base',review_enabled='false',ocr_enabled='false');self.assertTrue(override['uses_local_compute'])
        self.assertEqual(settings.binding(chat,'generate')['source'],'provider')
        with self.assertRaises(Exception):settings.binding(chat,'speech')
        bad={**settings.read()['models'],'speech':chat};response=self.client.patch('/api/settings',json={**{k:settings.read()[k] for k in ('revision','review_enabled','ocr_enabled','review_policy','trust')},'models':bad});self.assertEqual(response.status_code,422)
    def test_template_roundtrip_exports_active_and_conflict(self):
        config=settings.read();original=quiz.format_prompt()
        response=self.client.post('/api/settings/templates',json={'revision':config['revision'],'purpose':'quiz_format','name':'我的出题风格','text':original+'\n重点考查因果推理。'});self.assertEqual(response.status_code,200)
        data=response.json()['settings'];identity=data['active_templates']['quiz_format'];self.assertNotEqual(identity,'builtin')
        self.assertIn('重点考查因果推理',study_prompt({'filename':'test','segments':[]}))
        stale=self.client.post('/api/settings/templates',json={'revision':0,'purpose':'speech','name':'stale','text':'stale'});self.assertEqual(stale.status_code,409)
        protected=self.client.post('/api/settings/templates',json={'revision':data['revision'],'purpose':'speech','id':'builtin','name':'overwrite','text':'overwrite'});self.assertEqual(protected.status_code,422)
        changed=self.client.post('/api/settings/templates',json={'revision':data['revision'],'purpose':'quiz_format','id':identity,'name':'更新','text':'自定义新版'});self.assertEqual(changed.status_code,200)
        self.assertEqual(quiz.format_prompt(),'自定义新版')
        restored=self.client.patch('/api/settings/templates/active',json={'revision':changed.json()['settings']['revision'],'purpose':'quiz_format','id':'builtin'});self.assertEqual(restored.status_code,200);self.assertEqual(quiz.format_prompt(),original)
        self.assertEqual(len(settings.read()['templates']['quiz_format']),2)
    def test_disabled_stages_do_not_resolve_deleted_connections(self):
        data=settings.read();data.update(review_enabled=False,ocr_enabled=False);data['models']['review']='provider:missing';data['models']['vision']='provider:missing';settings.write(data)
        job=self.upload(model='base');self.assertEqual(job['review_binding']['source'],'disabled');self.assertEqual(job['vision_binding']['source'],'disabled')
    def test_nonchat_profile_rejected_for_question_generation(self):
        identity=self.profile(['transcription'])[9:]
        response=self.client.post('/api/ai/courses/irrelevant/generate',json={'profile_id':identity});self.assertEqual(response.status_code,422)
    def test_only_speech_and_vision_capabilities_expose_correct_options(self):
        audio=self.profile(['transcription']);vision=self.profile(['vision']);options=settings.model_options()
        self.assertIn(audio,[o['value'] for o in options['speech']]);self.assertNotIn(audio,[o['value'] for o in options['generate']]);self.assertIn(vision,[o['value'] for o in options['vision']])
    def test_default_function_routing_and_custom_prompts_reach_sdk_messages(self):
        first=self.profile(['chat']);second=self.profile(['chat'])
        models=settings.read()['models'];models.update(generate=first,analysis=second);self.edit(models=models)
        for purpose,text in [('quiz_format','题集协议自定义测试'),('generation_system','我的出题系统'),('analysis_system','我的分析系统'),('analysis_request','我的逐题分析要求')]:
            response=self.client.post('/api/settings/templates',json={'revision':settings.read()['revision'],'purpose':purpose,'name':'测试模板','text':text});self.assertEqual(response.status_code,200)
        job=app.new_job('transcribe','lesson.mp4','base','zh');job.update(status='completed',edited_text='课程知识点');app.write_job(job)
        with patch.object(ai,'schedule',return_value={'ok':True}) as call:
            result=self.client.post('/api/ai/courses/'+job['id']+'/generate',json={});self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(call.call_args.args[1]['id'],first[9:]);messages=call.call_args.args[2]
            self.assertEqual(messages[0]['content'],'我的出题系统');self.assertIn('题集协议自定义测试',messages[1]['content'])
        record=self.client.post('/api/quizzes',json={'content':json.dumps(quiz.example()),'source_job_id':job['id']}).json()
        record=self.client.post('/api/quizzes/'+record['id']+'/submit',json={'revision':record['revision'],'answers':{}}).json()
        with patch.object(ai,'schedule',return_value={'ok':True}) as call:
            result=self.client.post('/api/ai/quizzes/'+record['id']+'/analyze',json={'revision':record['revision']});self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(call.call_args.args[1]['id'],second[9:]);messages=call.call_args.args[2]
            self.assertEqual(messages[0]['content'],'我的分析系统');self.assertIn('我的逐题分析要求',messages[1]['content'])

    def test_mimo_secondary_rejects_unsupported_language(self):
        result=self.client.post('/api/jobs',data={'model':'base','review_model':'local:mimo-v2.5','review_enabled':'true','language':'ja'},files={'file':('test.mp4',b'fixture')});self.assertEqual(result.status_code,422)


class ReviewPolicyTests(unittest.TestCase):
    def test_trust_each_language_preserves_originals_and_exports_disagreements(self):
        for language,choice in [('zh','primary'),('en','review'),('mixed','primary')]:
            segment={'text':'他说 He go.','review_text':'他说 He goes.','review_state':'needs_review','start':0}
            job={'language':language,'review_policy':'by_language','trust':{'zh':'primary','en':'review','mixed':'primary'},'segments':[segment]}
            apply_review_policy(job,segment);self.assertEqual(segment['selected_source'],choice);self.assertEqual(segment['review_state'],'auto_selected')
            self.assertEqual(segment['text'],'他说 He go.');self.assertEqual(segment['review_text'],'他说 He goes.')
            self.assertIn('未经人工确认',review_material(job))
    def test_manual_or_empty_reference_cannot_overwrite(self):
        config={'language':'en','review_policy':'by_language','trust':{'en':'review'}}
        for extra in [{'resolution':'primary'},{'resolution':'confirm'}]:
            segment={'text':'original','review_text':'replacement','review_state':'confirmed',**extra};before=copy.deepcopy(segment);apply_review_policy(config,segment);self.assertEqual(before,segment)
        segment={'text':'original','review_text':'','review_state':'needs_review'};apply_review_policy(config,segment);self.assertNotIn('resolved_text',segment)
        segment['review_text']='replacement';apply_review_policy({**config,'edited_text':'user edited'},segment);self.assertNotIn('resolved_text',segment)
    def test_auto_language_and_no_review_policy(self):
        for text,expected in [('中文','zh'),('English','en'),('中文 English','mixed')]:
            segment={'text':text,'review_text':text,'review_state':'consistent'}
            apply_review_policy({'language':'auto','review_policy':'by_language','trust':{}},segment);self.assertEqual(segment['selected_language'],expected)
        segment={'text':'a','review_text':'b','review_state':'needs_review'};apply_review_policy({'review_policy':'manual'},segment);self.assertNotIn('resolved_text',segment)


if __name__=='__main__':unittest.main()
