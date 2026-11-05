"""Book / chapter organization stored transactionally with Python's SQLite."""
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import sqlite3
import threading
import uuid
import warnings

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError
import project_env

DB = project_env.DATA / 'library' / 'library.sqlite3'
COVERS = project_env.DATA / 'library' / 'covers'
LOCK = threading.RLock()
router = APIRouter(prefix='/api/library')
job_reader = None
THEMES = {'sage', 'violet', 'ochre', 'ocean', 'rose', 'ink'}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    with LOCK:
        DB.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DB, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript('''
        CREATE TABLE IF NOT EXISTS books (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          theme TEXT NOT NULL DEFAULT 'sage', cover TEXT, position INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS categories (
          id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id),
          parent_id TEXT REFERENCES categories(id), name TEXT NOT NULL,
          position INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS courses (
          job_id TEXT PRIMARY KEY, book_id TEXT REFERENCES books(id),
          category_id TEXT REFERENCES categories(id), title TEXT NOT NULL DEFAULT '',
          learned_at TEXT, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS category_parent ON categories(book_id, parent_id);
        CREATE INDEX IF NOT EXISTS course_book ON courses(book_id, category_id);
        ''')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def find(connection, table, identity):
    row = connection.execute(f'SELECT * FROM {table} WHERE id = ?', (identity,)).fetchone()
    if row is None:
        raise HTTPException(404, '书本或分类不存在')
    return dict(row)


def check_name(value):
    value = value.strip()
    if not value:
        raise HTTPException(422, '名称不能为空')
    return value


def location(connection, book_id, category_id):
    if category_id and not book_id:
        raise HTTPException(422, '请先选择所属书本')
    if book_id:
        find(connection, 'books', book_id)
    if category_id:
        if find(connection, 'categories', category_id)['book_id'] != book_id:
            raise HTTPException(422, '分类不属于这本书')


def assign(job_id, book_id=None, category_id=None, title=None, learned=None):
    with database() as c:
        location(c, book_id, category_id)
        old = c.execute('SELECT * FROM courses WHERE job_id = ?', (job_id,)).fetchone()
        title = title if title is not None else old['title'] if old else ''
        learned_at = (now() if learned else None) if learned is not None else old['learned_at'] if old else None
        c.execute('''INSERT INTO courses VALUES (?, ?, ?, ?, ?, ?)
          ON CONFLICT(job_id) DO UPDATE SET book_id=excluded.book_id,category_id=excluded.category_id,
          title=excluded.title,learned_at=excluded.learned_at,updated_at=excluded.updated_at''',
          (job_id, book_id, category_id, title.strip(), learned_at, now()))
    return {'job_id':job_id, 'book_id':book_id, 'category_id':category_id, 'title':title.strip(), 'learned_at':learned_at}


def forget(job_id):
    with database() as c:
        c.execute('DELETE FROM courses WHERE job_id = ?', (job_id,))


class BookEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    theme: str = 'sage'


class Reorder(BaseModel):
    ids: list[str] = Field(max_length=1000)


class CategoryCreate(BaseModel):
    book_id: str
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None


class CategoryEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None
    position: int = Field(default=0, ge=0, le=10000)


class CourseEdit(BaseModel):
    book_id: str | None = None
    category_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    learned: bool | None = None


def check_tree(c, book_id, node_id, parent_id):
    nodes = {r['id']:dict(r) for r in c.execute('SELECT * FROM categories WHERE book_id=?', (book_id,))}
    depth = 1
    cursor = parent_id
    seen = {node_id}
    while cursor:
        if cursor in seen:
            raise HTTPException(422, '不能把分类移入自己或自己的子分类')
        if cursor not in nodes:
            raise HTTPException(422, '上级分类必须在同一本书内')
        seen.add(cursor)
        depth += 1
        cursor = nodes[cursor]['parent_id']
    height = 0
    pending = [(node_id,0)]
    while pending:
        parent,h = pending.pop()
        height = max(height,h)
        pending.extend((n['id'],h+1) for n in nodes.values() if n['parent_id']==parent)
    if depth + height > 8:
        raise HTTPException(422, '书内最多支持 8 层分类，请将此分类放到更浅的位置')


@router.get('')
def snapshot():
    with database() as c:
        return {'books':[dict(r) for r in c.execute('SELECT * FROM books ORDER BY position, created_at')],
                'categories':[dict(r) for r in c.execute('SELECT * FROM categories ORDER BY position, id')],
                'courses':[dict(r) for r in c.execute('SELECT * FROM courses')]}


@router.post('/books')
def create_book(body: BookEdit):
    if body.theme not in THEMES:
        raise HTTPException(422, '封面配色无效')
    with database() as c:
        identity = str(uuid.uuid4())
        position = c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM books').fetchone()[0]
        c.execute('INSERT INTO books VALUES (?, ?, ?, ?, NULL, ?, ?, ?)',
                  (identity,check_name(body.name),body.description.strip(),body.theme,position,now(),now()))
        return find(c,'books',identity)


@router.patch('/books/order')
def reorder(body: Reorder):
    with database() as c:
        ids = {r[0] for r in c.execute('SELECT id FROM books')}
        if len(body.ids)!=len(set(body.ids)) or set(body.ids)!=ids:
            raise HTTPException(409, '书架发生变化，请刷新后重新排序')
        for position,identity in enumerate(body.ids):
            c.execute('UPDATE books SET position=? WHERE id=?',(position,identity))
    return {'ok':True}


@router.patch('/books/{book_id}')
def edit_book(book_id: str, body: BookEdit):
    if body.theme not in THEMES:
        raise HTTPException(422, '封面配色无效')
    with database() as c:
        find(c,'books',book_id)
        c.execute('UPDATE books SET name=?,description=?,theme=?,updated_at=? WHERE id=?',
                  (check_name(body.name),body.description.strip(),body.theme,now(),book_id))
        return find(c,'books',book_id)


@router.post('/books/{book_id}/cover')
async def upload_cover(book_id: str, file: UploadFile = File(...)):
    with database() as c:
        find(c,'books',book_id)
    try:
        data = await file.read(5*1024*1024+1)
        if len(data)>5*1024*1024:
            raise HTTPException(413,'封面不能超过 5 MB')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {'JPEG','PNG','WEBP'} or original.width*original.height>20_000_000:
                    raise HTTPException(422,'请选择 2000 万像素以内的 JPG、PNG 或 WebP 图片')
                image = ImageOps.exif_transpose(original)
                image.thumbnail((900,1200))
                # Flatten transparency onto paper, strip EXIF and arbitrary embedded content.
                rgb = Image.new('RGB',image.size,'#f4efe5')
                if image.mode in {'RGBA','LA'} or 'transparency' in image.info:
                    rgba=image.convert('RGBA');rgb.paste(rgba,mask=rgba.getchannel('A'))
                else:
                    rgb.paste(image.convert('RGB'))
                out=BytesIO();rgb.save(out,'JPEG',quality=88)
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning) as error:
        raise HTTPException(422,'无法读取封面，请选择有效的 JPG、PNG 或 WebP 图片') from error
    finally:
        await file.close()
    with database() as c:
        find(c,'books',book_id)
        COVERS.mkdir(parents=True,exist_ok=True)
        name=f'{book_id}-{uuid.uuid4().hex}.jpg'
        (COVERS/name).write_bytes(out.getvalue())
        old=find(c,'books',book_id)['cover']
        c.execute('UPDATE books SET cover=?,updated_at=? WHERE id=?',(name,now(),book_id))
        if old:
            (COVERS/Path(old).name).unlink(missing_ok=True)
    return {'ok':True}


@router.get('/books/{book_id}/cover')
def cover(book_id: str):
    with database() as c:
        book=find(c,'books',book_id)
    if not book['cover'] or not (COVERS/Path(book['cover']).name).is_file():
        raise HTTPException(404,'尚未设置封面')
    return FileResponse(COVERS/Path(book['cover']).name,media_type='image/jpeg')


@router.delete('/books/{book_id}/cover')
def clear_cover(book_id: str):
    with database() as c:
        find(c,'books',book_id)
        c.execute('UPDATE books SET cover=NULL,updated_at=? WHERE id=?',(now(),book_id))
    return {'ok':True}


@router.post('/categories')
def create_category(body: CategoryCreate):
    with database() as c:
        find(c,'books',body.book_id)
        identity=str(uuid.uuid4())
        check_tree(c,body.book_id,identity,body.parent_id)
        position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM categories WHERE book_id=? AND parent_id IS ?', (body.book_id,body.parent_id)).fetchone()[0]
        c.execute('INSERT INTO categories VALUES (?, ?, ?, ?, ?, ?)',(identity,body.book_id,body.parent_id,check_name(body.name),position,now()))
        return find(c,'categories',identity)


@router.patch('/categories/{category_id}')
def edit_category(category_id: str, body: CategoryEdit):
    with database() as c:
        node=find(c,'categories',category_id)
        check_tree(c,node['book_id'],category_id,body.parent_id)
        siblings=[r['id'] for r in c.execute('SELECT id FROM categories WHERE book_id=? AND parent_id IS ? AND id != ? ORDER BY position,id', (node['book_id'],body.parent_id,category_id))]
        siblings.insert(min(body.position,len(siblings)),category_id)
        c.execute('UPDATE categories SET parent_id=?,name=?,updated_at=? WHERE id=?',(body.parent_id,check_name(body.name),now(),category_id))
        for position,identity in enumerate(siblings):
            c.execute('UPDATE categories SET position=? WHERE id=?',(position,identity))
        return find(c,'categories',category_id)


@router.delete('/categories/{category_id}')
def delete_category(category_id: str):
    with database() as c:
        find(c,'categories',category_id)
        if c.execute('SELECT 1 FROM categories WHERE parent_id=?',(category_id,)).fetchone() or c.execute('SELECT 1 FROM courses WHERE category_id=?',(category_id,)).fetchone():
            raise HTTPException(409,'分类内还有课程或子分类，请先移出内容再删除')
        c.execute('DELETE FROM categories WHERE id=?',(category_id,))
    return {'ok':True}


@router.patch('/courses/{job_id}')
def edit_course(job_id: str, body: CourseEdit):
    job=job_reader(job_id)
    if job.get('kind')!='transcribe':
        raise HTTPException(422,'模型下载记录不能归入课程')
    return assign(job_id,body.book_id,body.category_id,body.title,body.learned)
