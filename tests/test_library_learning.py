import copy
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import library
import quiz


class LibraryLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=app.project_env.DATA/'tmp')
        self.root=Path(self.tmp.name)
        (self.root/'jobs').mkdir()
        self.patches=[patch.object(library,'DB',self.root/'library.sqlite3'),patch.object(library,'COVERS',self.root/'covers'),
                      patch.object(app,'JOBS',self.root/'jobs'),patch.object(quiz,'QUIZZES',self.root/'quizzes')]
        for p in self.patches:p.start()
        self.client=TestClient(app.app)
        self.book=self.client.post('/api/library/books',json={'name':'英语语法','theme':'sage'}).json()
        self.job=app.new_job('transcribe','lesson.mp4','base','en')
        self.job.update(status='completed',segments=[{'id':0,'start':0,'end':3,'text':'Original lesson content'}],edited_text='校对后的课程内容')
        app.write_job(self.job)

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def category(self,name,parent=None,book=None):
        response=self.client.post('/api/library/categories',json={'name':name,'parent_id':parent,'book_id':(book or self.book)['id']})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def create_quiz(self):
        response=self.client.post('/api/quizzes',json={'content':json.dumps(quiz.example()),'source_job_id':self.job['id']})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_existing_course_survives_organization_and_book_updates(self):
        first=self.category('第一章');second=self.category('时态',first['id'])
        response=self.client.patch('/api/library/courses/'+self.job['id'],json={'book_id':self.book['id'],'category_id':second['id'],'title':'第 1 课：一般现在时','learned':True})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['learned_at'])
        self.assertEqual(app.read_job(self.job['id'])['edited_text'],'校对后的课程内容')
        self.assertEqual(self.client.delete('/api/library/categories/'+first['id']).status_code,409)
        self.assertEqual(self.client.delete('/api/library/categories/'+second['id']).status_code,409)
        self.client.patch('/api/library/books/'+self.book['id'],json={'name':'英语学习','theme':'ocean','description':'语法与应用'})
        snapshot=self.client.get('/api/library').json()
        self.assertEqual(snapshot['books'][0]['name'],'英语学习')
        self.assertEqual(snapshot['courses'][0]['category_id'],second['id'])
        self.client.patch('/api/library/courses/'+self.job['id'],json={})
        self.assertEqual(self.client.delete('/api/library/categories/'+second['id']).status_code,200)
        self.assertEqual(self.client.delete('/api/library/categories/'+first['id']).status_code,200)

    def test_tree_rejects_cycles_cross_book_and_too_deep_subtree(self):
        parent=self.category('1');child=self.category('2',parent['id'])
        response=self.client.patch('/api/library/categories/'+parent['id'],json={'name':'1','parent_id':child['id']})
        self.assertEqual(response.status_code,422)
        other=self.client.post('/api/library/books',json={'name':'数学'}).json()
        foreign=self.category('外部',book=other)
        self.assertEqual(self.client.patch('/api/library/categories/'+child['id'],json={'name':'2','parent_id':foreign['id']}).status_code,422)
        tip=child
        for depth in range(3,9):tip=self.category(str(depth),tip['id'])
        self.assertEqual(self.client.post('/api/library/categories',json={'name':'第9层','parent_id':tip['id'],'book_id':self.book['id']}).status_code,422)
        subtree=self.category('两层子树');self.category('子级',subtree['id'])
        self.assertEqual(self.client.patch('/api/library/categories/'+subtree['id'],json={'name':'移动','parent_id':tip['id']}).status_code,422)
        self.assertEqual(self.client.patch('/api/library/courses/'+self.job['id'],json={'book_id':other['id'],'category_id':child['id']}).status_code,422)

    def test_reorder_is_transactional_and_empty_names_rejected(self):
        other=self.client.post('/api/library/books',json={'name':'数学'}).json()
        order=[other['id'],self.book['id']]
        self.assertEqual(self.client.patch('/api/library/books/order',json={'ids':order}).status_code,200)
        self.assertEqual([b['id'] for b in self.client.get('/api/library').json()['books']],order)
        self.assertEqual(self.client.patch('/api/library/books/order',json={'ids':[order[0],order[0]]}).status_code,409)
        self.assertEqual(self.client.post('/api/library/books',json={'name':'   '}).status_code,422)

    def test_cover_is_local_reencoded_and_invalid_images_rejected(self):
        buffer=BytesIO();Image.new('RGBA',(300,450),(50,90,75,160)).save(buffer,'PNG')
        base='/api/library/books/'+self.book['id']+'/cover'
        result=self.client.post(base,files={'file':('封面.png',buffer.getvalue(),'image/png')})
        self.assertEqual(result.status_code,200,result.text)
        response=self.client.get(base)
        self.assertEqual(response.headers['content-type'],'image/jpeg')
        with Image.open(BytesIO(response.content)) as image:self.assertEqual(image.mode,'RGB')
        self.assertEqual(self.client.post(base,files={'file':('x.svg',b'<svg></svg>','image/svg+xml')}).status_code,422)
        self.assertEqual(self.client.post(base,files={'file':('big.png',b'x'*(5*1024*1024+1),'image/png')}).status_code,413)
        self.assertEqual(self.client.delete(base).status_code,200)
        self.assertEqual(self.client.get(base).status_code,404)

    def test_upload_can_target_a_chapter_before_queueing(self):
        chapter=self.category('第一章')
        with patch.object(app.TASKS,'put'):
            response=self.client.post('/api/jobs',data={'model':'base','book_id':self.book['id'],'category_id':chapter['id']},files={'file':('new.mp4',b'media')})
        self.assertEqual(response.status_code,200,response.text)
        placement=next(p for p in self.client.get('/api/library').json()['courses'] if p['job_id']==response.json()['id'])
        self.assertEqual(placement['category_id'],chapter['id'])
        self.assertEqual(self.client.post('/api/jobs',data={'book_id':self.book['id'],'category_id':'missing'},files={'file':('bad.mp4',b'media')}).status_code,404)

    def test_course_quiz_source_feedback_and_staleness(self):
        library.assign(self.job['id'],self.book['id'],title='我的第一课')
        record=self.create_quiz();base='/api/quizzes/'+record['id']
        self.assertEqual(record['source_job_id'],self.job['id'])
        self.assertEqual(record['source']['title'],'我的第一课')
        self.assertNotIn('text',record['source'])
        self.assertEqual(self.client.get('/api/quizzes').json()[0]['source_job_id'],self.job['id'])
        record=self.client.post(base+'/submit',json={'revision':record['revision'],'answers':{'q1':'B','q5':'我的解释'}}).json()
        record=self.client.patch(base+'/analysis',json={'revision':record['revision'],'text':'薄弱点：主动回忆。明天闭卷练习。'}).json()
        self.assertFalse(record['analysis_stale'])
        report=self.client.get(base+'/export/responses').text
        self.assertIn('校对后的课程内容',report);self.assertIn('我的第一课',report);self.assertIn('我的解释',report)
        record=self.client.patch(base+'/answers',json={'revision':record['revision'],'answers':{'q1':'A'}}).json()
        self.assertTrue(record['analysis_stale'])
        self.assertIn('薄弱点',record['analysis']['text'])
        self.assertEqual(self.client.patch(base+'/analysis',json={'revision':0,'text':'旧页面'}).status_code,409)
        # Original course can disappear; the evidence and practice record remain usable.
        self.client.delete('/api/jobs/'+self.job['id'])
        self.assertEqual(self.client.get(base).status_code,200)
        self.assertIn('校对后的课程内容',self.client.get(base+'/export/responses').text)

    def test_attach_legacy_quiz_and_cross_site_rejection(self):
        record=self.client.post('/api/quizzes',json={'content':json.dumps(quiz.example())}).json()
        base='/api/quizzes/'+record['id']
        result=self.client.patch(base+'/source',json={'source_job_id':self.job['id'],'revision':0})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['source_job_id'],self.job['id'])
        self.assertEqual(self.client.post('/api/library/books',json={'name':'bad'},headers={'Origin':'https://bad.example'}).status_code,403)
        self.assertEqual(self.client.patch('/api/library/courses/missing',json={}).status_code,404)

if __name__=='__main__':unittest.main()
