import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import quiz
from fastapi.testclient import TestClient
from exports import study_prompt


class QuizRulesTests(unittest.TestCase):
    def setUp(self):
        self.quiz = quiz.validate_quiz(quiz.example())

    def test_all_types_and_exact_multiselect(self):
        answers = {'q1':'A','q2':['C','A'],'q3':False,'q4':['ＡＩ','3；2','3.14'],'q5':'先复述，再测试和纠错'}
        result = quiz.grade(self.quiz, quiz.validate_answers(self.quiz, answers))
        self.assertEqual((result['auto_score'],result['auto_total'],result['manual_total']),(8,8,4))
        self.assertEqual(result['questions']['q5']['score'],None)
        for wrong in [['A'], ['A','B','C'], []]:
            self.assertEqual(quiz.grade(self.quiz, {'q2':wrong})['questions']['q2']['score'],0)

    def test_alternatives_case_and_whitespace(self):
        rule = {'mode':'any_of','values':['artificial intelligence','AI'],'case_sensitive':False}
        self.assertTrue(quiz.match_blank('  ARTIFICIAL   intelligence  ',rule))
        self.assertFalse(quiz.match_blank('AI 或者其他',rule))
        self.assertFalse(quiz.match_blank('AI.',rule))
        rule['case_sensitive'] = True
        self.assertFalse(quiz.match_blank('ai',rule))

    def test_all_required_rejects_extra_duplicate_and_missing(self):
        rule = {'mode':'all_of','values':['alpha','beta'],'case_sensitive':False}
        self.assertTrue(quiz.match_blank('Beta\nALPHA',rule))
        for value in ['alpha','alpha;beta;gamma','alpha;beta;alpha','', 'alpha,beta']:
            self.assertFalse(quiz.match_blank(value,rule))

    def test_numeric_boundaries_and_non_numbers(self):
        rule = {'mode':'number_range','min':3.13,'max':3.15}
        for value in ['3.13','3.15','３.１４',' 3.14 ','314e-2']:
            self.assertTrue(quiz.match_blank(value,rule))
        for value in ['3.12999','3.150001','NaN','Infinity','3.14kg','3.14%','3,14','']:
            self.assertFalse(quiz.match_blank(value,rule))

    def test_partial_blanks_and_unanswered_false(self):
        result = quiz.grade(self.quiz, {'q3':False,'q4':['ai','','9']})
        self.assertEqual(result['questions']['q4']['status'],'partial')
        self.assertEqual(result['questions']['q4']['score'],1)
        self.assertTrue(result['questions']['q3']['answered'])
        self.assertEqual(quiz.grade(self.quiz, {})['questions']['q3']['status'],'unanswered')
        self.assertEqual(quiz.grade(self.quiz, {'q4':['','','']})['answered'],0)

    def test_fences_and_bad_schema(self):
        self.assertEqual(quiz.parse_quiz('```json\n'+json.dumps(self.quiz)+'\n```'),self.quiz)
        for change in [lambda d:d.update(version=True),lambda d:d['questions'].append(d['questions'][0]),
                       lambda d:d['questions'][0].update(answer='Z'),lambda d:d['questions'][2].update(answer='false'),
                       lambda d:d['questions'][3]['blanks'][2]['answer'].update(min=4),
                       lambda d:d['questions'][0].update(points=float('nan')),
                       lambda d:d['questions'][1].update(answer=['A','A']),
                       lambda d:d['questions'][0].update(id='../escape'),
                       lambda d:d['questions'][3]['blanks'][0]['answer'].update(values=['AI','ai'])]:
            data=copy.deepcopy(self.quiz);change(data)
            with self.assertRaises(ValueError):quiz.validate_quiz(data)
        with self.assertRaisesRegex(ValueError,'第 1 行'):quiz.parse_quiz('{broken}')
        with self.assertRaisesRegex(ValueError,'重复'):quiz.parse_quiz('{"version":1,"version":2}')

    def test_prompt_includes_import_protocol_and_all_sources(self):
        prompt = study_prompt({'filename':'test.mp4','edited_text':'课程正文','segments':[], 'visuals':[{'start':2,'text':'画面正文'}]})
        for piece in ['number_range','any_of','all_of','multiple_choice','short_answer','课程正文','画面正文','默认隐藏']:
            self.assertIn(piece,prompt)
        self.assertNotIn('先只发题目',prompt)


class QuizAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=app.project_env.DATA/'tmp')
        self.patcher = patch.object(quiz,'QUIZZES',Path(self.tmp.name))
        self.patcher.start()
        self.client=TestClient(app.app)
        self.record=self.client.post('/api/quizzes',json={'content':json.dumps(quiz.example(),ensure_ascii=False)}).json()
        self.base='/api/quizzes/'+self.record['id']

    def tearDown(self):
        self.client.close();self.patcher.stop();self.tmp.cleanup()

    def test_hidden_by_default_and_individual_reveal(self):
        for q in self.record['quiz']['questions']:
            self.assertNotIn('answer',q);self.assertNotIn('explanation',q)
            for blank in q.get('blanks',[]):self.assertNotIn('answer',blank)
        r=self.client.post(self.base+'/reveal',json={'question_id':'q4','show':True}).json()
        self.assertIn('answer',r['quiz']['questions'][3]['blanks'][0])
        self.assertNotIn('answer',r['quiz']['questions'][0])
        self.assertEqual(r['seen'],['q4'])
        r=self.client.post(self.base+'/reveal',json={'show':False}).json()
        self.assertEqual(r['revealed'],[]);self.assertEqual(r['seen'],['q4'])
        r=self.client.post(self.base+'/reveal',json={'show':True}).json()
        self.assertEqual(len(r['revealed']),5)
        self.assertEqual(self.client.post(self.base+'/reveal',json={'question_id':'bad'}).status_code,422)

    def test_save_submit_restart_edit_and_conflict(self):
        body={'answers':{'q1':'A','q2':['A','C'],'q3':False,'q4':['ai','2;3','3.14'],'q5':'我的独立作答'},'revision':0}
        r=self.client.patch(self.base+'/answers',json=body).json()
        self.assertIsNone(r['result'])
        self.assertEqual(self.client.get(self.base).json()['answers'],body['answers'])
        self.assertEqual(quiz.load(r['id'])['answers'],body['answers'])
        self.assertEqual(self.client.patch(self.base+'/answers',json=body).status_code,409)
        body['revision']=r['revision']
        r=self.client.post(self.base+'/submit',json=body).json()
        self.assertEqual(r['result']['auto_score'],8)
        self.assertNotIn('answer',r['quiz']['questions'][0])
        listed=self.client.get('/api/quizzes').json()[0]
        self.assertEqual(listed['answered'],5);self.assertTrue(listed['submitted'])
        body['revision']=r['revision'];body['answers']['q1']='B'
        r=self.client.patch(self.base+'/answers',json=body).json()
        self.assertIsNone(r['result']);self.assertIsNone(r['submitted_at'])

    def test_bad_input_does_not_replace_answers(self):
        for answers in [{'q3':'false'},{'q4':['ai']},{'q2':['Z']},{'bad':'A'},{'q1':False}]:
            self.assertEqual(self.client.patch(self.base+'/answers',json={'revision':0,'answers':answers}).status_code,422)
        self.assertEqual(self.client.get(self.base).json()['revision'],0)
        self.assertEqual(self.client.post('/api/quizzes',json={'content':'not json'}).status_code,422)
        self.assertEqual(self.client.get('/api/quizzes/not-a-uuid').status_code,404)
        self.assertEqual(self.client.post(self.base+'/reveal',json={},headers={'Origin':'https://evil.example'}).status_code,403)

    def test_exports_include_question_local_answers_and_my_response(self):
        self.client.post(self.base+'/submit',json={'revision':0,'answers':{'q3':False,'q4':['AI','2;3','3.14'],'q5':'我的中文 English 作答'}})
        response=self.client.get(self.base+'/export/responses')
        self.assertEqual(response.status_code,200)
        for piece in ['我的中文 English 作答','主动回忆','参考答案','解析','必须答全','待 AI 评价','自动判分','我的作答']:
            self.assertIn(piece,response.text)
        self.assertIn('filename*=UTF-8',response.headers['content-disposition'])
        answers=self.client.get(self.base+'/export/answers').text
        self.assertNotIn('我的中文 English 作答',answers)
        self.assertLess(answers.index('参考答案：'),answers.index('## 2.'))
        self.assertEqual(self.client.get(self.base+'/export/json').json()['answers']['q3'],False)
        self.assertEqual(self.client.get(self.base+'/export/unknown').status_code,404)


if __name__=='__main__': unittest.main()
