"""Use the upstream LiteLLM SDK for provider adapters; own only app state/workflows."""
import project_env
import asyncio
import copy
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import threading
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
import quiz

# Apply before the lazy SDK import: bundled catalogs, no incidental catalog/telemetry traffic.
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['LITELLM_OFFLINE_MODE'] = 'true'
os.environ['LITELLM_LOG'] = 'ERROR'
os.environ['DO_NOT_TRACK'] = '1'
os.environ['TIKTOKEN_CACHE_DIR'] = str(project_env.ROOT / '.cache' / 'tiktoken')
SETTINGS = Path(os.environ.get('SHIKE_AI_CONFIG',str(project_env.DATA / 'ai' / 'providers.json')))
TASKS_DIR = project_env.DATA / 'ai' / 'tasks'
LOCK = threading.RLock()
SDK_LOCK = threading.Lock()
SDK = None
RUNNING = {}
router = APIRouter(prefix='/api/ai')
PRESETS = [
    {'id':'native','name':'LiteLLM 原生供应商（完整目录）','provider':'','base_url':''},
    {'id':'compatible','name':'任意 OpenAI 兼容接口','provider':'openai','base_url':''},
    {'id':'deepseek','name':'DeepSeek','provider':'deepseek','base_url':''},
    {'id':'qwen','name':'阿里云百炼 / 通义千问（兼容接口）','provider':'openai','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1'},
    {'id':'moonshot','name':'Moonshot / Kimi（兼容接口）','provider':'openai','base_url':'https://api.moonshot.cn/v1'},
    {'id':'openrouter','name':'OpenRouter','provider':'openrouter','base_url':''},
    {'id':'openai','name':'OpenAI','provider':'openai','base_url':''},
    {'id':'anthropic','name':'Anthropic / Claude','provider':'anthropic','base_url':''},
    {'id':'gemini','name':'Google AI Studio / Gemini','provider':'gemini','base_url':''},
    {'id':'ollama','name':'本地 Ollama（兼容接口）','provider':'openai','base_url':'http://127.0.0.1:11434/v1'},
    {'id':'lmstudio','name':'本地 LM Studio（兼容接口）','provider':'openai','base_url':'http://127.0.0.1:1234/v1'},
]
# Application-owned invocation controls cannot be replaced by provider-specific JSON.
RESERVED = {'model','messages','prompt','input','stream','stream_options','timeout','request_timeout','num_retries',
            'max_retries','max_tokens','max_completion_tokens','api_key','api_base','base_url','custom_llm_provider',
            'metadata','client','tools','tool_choice','functions','function_call','litellm_logging_obj','logger_fn',
            'success_callback','failure_callback','callbacks','mock_response','mock_timeout','caching','cache',
            'fallbacks','context_window_fallbacks','acompletion','completion','user','model_list','deployment_id'}


def sdk():
    global SDK
    with SDK_LOCK:
        if SDK is None:
            import litellm
            litellm.telemetry = False
            litellm.turn_off_message_logging = True
            litellm.suppress_debug_info = True
            litellm.set_verbose = False
            for name in ('LiteLLM','LiteLLM Router','LiteLLM Proxy','openai','httpx','httpcore'):
                logging.getLogger(name).setLevel(logging.CRITICAL)
            SDK = litellm
    return SDK


def providers():
    return sorted({str(getattr(p,'value',p)) for p in sdk().provider_list})


def settings():
    if not SETTINGS.is_file():
        return {'profiles':{},'default_profile_id':None}
    try:
        data=json.loads(SETTINGS.read_text(encoding='utf-8'))
        if not isinstance(data.get('profiles'),dict):raise ValueError()
        return data
    except (ValueError,OSError,AttributeError) as error:
        raise HTTPException(500,'供应商配置文件无法读取，原文件已保留，请检查 data/ai/providers.json。') from error


def private_write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    os.chmod(path.parent,0o700)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            json.dump(data,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
        temp.replace(path);os.chmod(path,0o600)
    finally:
        temp.unlink(missing_ok=True)


def safe_profile(profile):
    return {k:v for k,v in profile.items() if k not in {'api_key','provider_params'}} | {
        'has_key':bool(profile.get('api_key')),'has_provider_params':bool(profile.get('provider_params'))}


def profile(identity):
    with LOCK:
        data=settings();identity=identity or data.get('default_profile_id')
        if identity not in data['profiles']:
            raise HTTPException(422,'请先在「模型供应商」中添加并选择一个连接。')
        return copy.deepcopy(data['profiles'][identity])


def local_address(host):
    if host in {'localhost','127.0.0.1','::1'}:return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def check_url(value):
    value=value.strip().rstrip('/')
    if not value:return ''
    parsed=urlsplit(value)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in value):
        raise HTTPException(422,'接口地址必须是 HTTP(S) 地址，不能包含账号、密码、查询参数或片段。')
    if parsed.scheme=='http' and not local_address(parsed.hostname):
        raise HTTPException(422,'远程供应商请使用 HTTPS；HTTP 仅用于本机或局域网模型服务。')
    return value


class ProfileBody(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    provider: str = Field(min_length=1,max_length=100)
    model: str = Field(min_length=1,max_length=300)
    base_url: str = Field(default='',max_length=2000)
    api_key: str | None = Field(default=None,max_length=8192,repr=False)
    provider_params: dict | None = Field(default=None,repr=False)
    clear_key: bool = False
    clear_provider_params: bool = False
    max_tokens: int = Field(default=8192,ge=128,le=65536)
    timeout: int = Field(default=300,ge=30,le=1800)
    capabilities: list[str] = Field(default_factory=lambda:['chat'],min_length=1,max_length=3)


class DefaultBody(BaseModel):
    default_profile_id: str


def validate_profile(body, old=None):
    name=body.name.strip();provider=body.provider.strip();model=body.model.strip()
    if not name or not model or provider not in providers():raise HTTPException(422,'名称和模型名不能为空；供应商标识需来自 LiteLLM 目录。')
    if any(c in model for c in '\r\n') or any(c in (body.api_key or '') for c in '\r\n'):
        raise HTTPException(422,'模型名与 API Key 不能包含换行。')
    if len(set(body.capabilities))!=len(body.capabilities) or any(c not in {'chat','transcription','vision'} for c in body.capabilities):raise HTTPException(422,'请选择正确的模型用途')
    url=check_url(body.base_url)
    params=body.provider_params
    if params is not None:
        try:encoded=json.dumps(params,ensure_ascii=False,allow_nan=False)
        except (ValueError,TypeError):raise HTTPException(422,'供应商参数必须是合法 JSON，不能包含 NaN 或 Infinity。')
        headers=params.get('extra_headers')
        if headers is not None and (not isinstance(headers,dict) or any(not isinstance(k,str) or not isinstance(v,str) or any(c in k+v for c in '\r\n') for k,v in headers.items())):
            raise HTTPException(422,'extra_headers 必须是文本键值对象，不能包含换行。')
        if len(encoded)>20000:raise HTTPException(422,'供应商参数最多 20000 字')
        if any(k in RESERVED or k in {'file','language','prompt','audio','input','response_format'} or re.search(r'callback|logger|logging|mock|cache|module|import',k,re.I) or not re.fullmatch(r'[a-z][a-z0-9_]*',k) for k in params):
            raise HTTPException(422,'供应商参数只能填写供应商要求的认证/区域/版本参数，不能覆盖模型、提示词、回调、请求地址或应用控制项。')
    changed=old and (old['provider']!=provider or old.get('base_url','')!=url)
    if changed and old.get('api_key') and not body.api_key and not body.clear_key:
        raise HTTPException(422,'更换供应商或接口地址时，请重新填写 API Key，避免将旧密钥发往新的服务。')
    if changed and old.get('provider_params') and params is None and not body.clear_provider_params:
        raise HTTPException(422,'更换服务时请重新填写或清除已保存的供应商参数。')
    key='' if body.clear_key else (body.api_key or '').strip() or (old or {}).get('api_key','')
    params={} if body.clear_provider_params else params if params is not None else (old or {}).get('provider_params',{})
    return {'id':(old or {}).get('id',str(uuid.uuid4())),'name':name,'provider':provider,'model':model,'base_url':url,'api_key':key,
            'capabilities':body.capabilities,'provider_params':params,'max_tokens':body.max_tokens,'timeout':body.timeout,'updated_at':quiz.now()}


@router.get('/settings')
def get_settings():
    with LOCK:
        data=settings()
        return {'profiles':[safe_profile(p) for p in data['profiles'].values()], 'default_profile_id':data.get('default_profile_id'),
                'providers':providers(),'presets':PRESETS,'sdk':'LiteLLM Core 1.108.0'}


@router.get('/catalog')
def catalog(provider: str):
    if provider not in providers():raise HTTPException(422,'供应商不在 SDK 目录中')
    names=[]
    for name,entry in sdk().model_cost.items():
        if isinstance(entry,dict) and entry.get('litellm_provider')==provider and entry.get('mode')=='chat':
            prefix=provider+'/'
            names.append(name[len(prefix):] if name.startswith(prefix) else name)
    return {'models':sorted(set(names))[:500],'source':'SDK 随包目录，可手填供应商控制台里的模型 ID，不代表实时可用权限。'}


@router.post('/profiles')
def add_profile(body: ProfileBody):
    saved=validate_profile(body)
    with LOCK:
        data=settings()
        if len(data['profiles'])>=50:raise HTTPException(422,'最多保存 50 个模型连接')
        data['profiles'][saved['id']]=saved
        if not data.get('default_profile_id'):data['default_profile_id']=saved['id']
        private_write(SETTINGS,data)
    return safe_profile(saved)


@router.patch('/profiles/{identity}')
def edit_profile(identity: str, body: ProfileBody):
    with LOCK:
        data=settings()
        if identity not in data['profiles']:raise HTTPException(404,'连接不存在')
        saved=validate_profile(body,data['profiles'][identity]);data['profiles'][identity]=saved;private_write(SETTINGS,data)
    return safe_profile(saved)


@router.patch('/settings')
def set_default(body: DefaultBody):
    with LOCK:
        data=settings()
        if body.default_profile_id not in data['profiles']:raise HTTPException(404,'连接不存在')
        data['default_profile_id']=body.default_profile_id;private_write(SETTINGS,data)
    return {'ok':True}


@router.delete('/profiles/{identity}')
def remove_profile(identity: str):
    with LOCK:
        data=settings()
        if identity not in data['profiles']:raise HTTPException(404,'连接不存在')
        if any(load_task(key)['profile_id']==identity for key in RUNNING):raise HTTPException(409,'请先取消该连接正在进行的调用，再删除连接。')
        del data['profiles'][identity]
        if data.get('default_profile_id')==identity:data['default_profile_id']=next(iter(data['profiles']),None)
        private_write(SETTINGS,data)
    return {'ok':True}


def task_path(identity):
    try:
        if str(uuid.UUID(identity))!=identity:raise ValueError()
    except (ValueError,AttributeError):raise HTTPException(404,'AI 任务不存在')
    return TASKS_DIR/(identity+'.json')


def load_task(identity):
    path=task_path(identity)
    if not path.is_file():raise HTTPException(404,'AI 任务不存在')
    return json.loads(path.read_text(encoding='utf-8'))


def save_task(task):
    private_write(task_path(task['id']),task)


def public_task(task):
    return {k:v for k,v in task.items() if k not in {'raw_output','source','input_answers'}} | {'has_output':bool(task.get('raw_output'))}


def safe_error(error):
    if isinstance(error,HTTPException):return str(error.detail)
    name=type(error).__name__
    status=getattr(error,'status_code',None)
    if 'Authentication' in name or status==401:return '认证失败，请检查 API Key、供应商、接口地址和模型权限。'
    if 'Permission' in name or status==403:return '供应商拒绝请求，请检查模型权限或区域限制。'
    if 'RateLimit' in name or status==429:return '供应商限流或额度不足，请检查控制台后再手动重试。'
    if isinstance(error,(TimeoutError,asyncio.TimeoutError)) or 'Timeout' in name:return '模型调用超时，未自动重试；可调长超时后再试，已保存的课程与作答不受影响。'
    if 'ContextWindow' in name:return '课程内容超过模型上下文，请使用更长上下文模型或拆分课程；应用没有截断学习资料。'
    if 'Connection' in name:return '无法连接模型服务，请检查接口地址、网络和本地服务是否已启动。'
    if 'NotFound' in name or status==404:return '模型或接口不存在，请核对模型 ID 和接口地址。'
    if 'BadRequest' in name or status==400:return '供应商未接受请求，请检查模型 ID、输出上限、上下文长度及高级供应商参数。'
    if 'ModuleNotFound' in name or 'ImportError' in name:return '该供应商需要额外的官方可选依赖，请按 LiteLLM 对应供应商文档安装到本项目虚拟环境。'
    return '模型调用未完成，请核对连接配置后重试。错误内容未写入日志，避免包含密钥或课程正文。'


async def model_call(connection,messages,test=False):
    client=await asyncio.to_thread(sdk)
    key=connection.get('api_key')
    no_key_local=connection.get('base_url') and local_address(urlsplit(connection['base_url']).hostname)
    credentials=connection.get('provider_params',{})
    if not key and not no_key_local and connection['provider'] not in {'ollama','ollama_chat','hosted_vllm','llamafile'} and not credentials:
        raise HTTPException(422,'此连接没有保存 API Key。请先在模型供应商中填写。')
    kwargs=dict(credentials)
    kwargs.update(model=connection['model'],custom_llm_provider=connection['provider'],messages=messages,stream=False,
                  max_tokens=min(connection['max_tokens'],128) if test else connection['max_tokens'],
                  timeout=connection['timeout'],num_retries=0,max_retries=0)
    if key:kwargs['api_key']=key
    elif no_key_local:kwargs['api_key']='local-no-key'
    if connection.get('base_url'):kwargs['api_base']=connection['base_url']
    # Own the overall deadline as well as the SDK's transport timeout.
    response=await asyncio.wait_for(client.acompletion(**kwargs),timeout=connection['timeout']+5)
    choice=response.choices[0]
    content=choice.message.content
    if isinstance(content,list):content='\n'.join(part.get('text','') for part in content if isinstance(part,dict))
    usage=response.usage.model_dump() if response.usage and hasattr(response.usage,'model_dump') else {}
    return {'text':content or '', 'finish_reason':choice.finish_reason,
            'usage':{k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens') if isinstance(usage.get(k),int)}}


async def test_model_call(connection,messages):
    capabilities=connection.get('capabilities',['chat'])
    if 'chat' in capabilities:return await model_call(connection,messages,True)
    if 'transcription' in capabilities:
        import numpy as np
        from remote_inference import RemoteSession
        text,_=await RemoteSession().speech_call(np.zeros(16000,dtype=np.float32),{'profile_id':connection['id']},{'language':'auto','prompt_snapshot':{'speech':'连接测试，静音片段，无可辨内容时返回空文本。'}})
        return {'text':text,'usage':{},'finish_reason':'stop'}
    # Tiny generated image, never a course screenshot.
    return await model_call(connection,[{'role':'user','content':[{'type':'text','text':'连接测试，图片是一张纯色图片，请回复 OK。'},{'type':'image_url','image_url':{'url':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII='}}]}],True)


class GenerateBody(BaseModel):
    profile_id: str | None = None
    count: int = Field(default=10,ge=1,le=30)
    instructions: str = Field(default='',max_length=4000)


class AnalyzeBody(BaseModel):
    profile_id: str | None = None
    revision: int = Field(ge=0)
    instructions: str = Field(default='',max_length=4000)


class TestBody(BaseModel):
    profile_id: str


def prepared_task(kind,connection,**values):
    return {'id':str(uuid.uuid4()),'kind':kind,'status':'queued','profile_id':connection['id'],'profile_name':connection['name'],
            'provider':connection['provider'],'model':connection['model'],'destination':connection.get('base_url') or connection['provider'],
            'message':'等待模型响应…','created_at':quiz.now(),'updated_at':quiz.now(),'raw_output':'','usage':{},**values}


async def run_task(task,connection,messages):
    try:
        task.update(status='running',message='模型正在生成题集…' if task['kind']=='generate' else '模型正在分析作答…' if task['kind']=='analysis' else '正在测试连接…',updated_at=quiz.now())
        with LOCK:save_task(task)
        response=await test_model_call(connection,messages) if task['kind']=='test' else await model_call(connection,messages)
        text=response['text'].strip();task.update(raw_output=text,usage=response['usage'],finish_reason=response['finish_reason'])
        if task['kind']=='test':
            task.update(status='completed',message='已收到模型服务的有效响应。'+('（输出为空，可调高输出上限后在实际任务中验证。）' if not text else ''))
        elif response['finish_reason'] in {'length','max_tokens'}:
            task.update(status='failed',message='模型输出达到上限，可能不完整；请提高输出上限或减少题数后重试。已保留原始输出。')
        elif not text:
            task.update(status='failed',message='模型没有返回可用的正文；请检查是否需要更高输出预算，或更换可输出文字的模型。')
        elif task['kind']=='generate':
            try:validated=quiz.parse_quiz(text)
            except (ValueError,RecursionError):
                task.update(status='failed',message='模型输出不符合题集格式，未导入任何半成品。可查看原始输出并手动修正导入，或重新生成。')
            else:
                record=quiz.create_record(validated,task['source_job_id'],task['source'],origin={'provider':task['provider'],'model':task['model'],'task_id':task['id']})
                task.update(status='completed',result_id=record['id'],message=f"已生成并保存 {len(validated['questions'])} 道题，答案默认隐藏。")
                if len(validated['questions'])!=task['requested_count']:task['message']+=f"（请求 {task['requested_count']} 道，模型实际返回 {len(validated['questions'])} 道。）"
        else:
            if len(text)>100000:task.update(status='failed',message='分析超过 100000 字，已保留原始输出，可下载查看。')
            else:
                with quiz.LOCK:
                    record=quiz.load(task['quiz_id'])
                    if record['revision']!=task['input_revision']:
                        task.update(status='conflict',message='生成期间作答或反馈发生变化，AI 结果已保留但未覆盖当前分析。可查看输出后自行采用。')
                    else:
                        if record.get('analysis'):record['analysis_history']=(record.get('analysis_history',[])+[record['analysis']])[-20:]
                        record['analysis']={'text':text,'answers':copy.deepcopy(task['input_answers']),'updated_at':quiz.now(),'origin':'provider','provider':task['provider'],'model':task['model'],'task_id':task['id']}
                        record.update(revision=record['revision']+1,updated_at=quiz.now());quiz.save(record)
                        task.update(status='completed',result_id=record['id'],message='AI 分析已保存到本课。原有反馈保留在完整记录的历史中。')
    except asyncio.CancelledError:
        task.update(status='cancelled',message='已停止等待并取消本地请求。供应商可能已处理部分请求，费用以其账单为准。')
    except Exception as error:
        task.update(status='failed',message=safe_error(error))
    finally:
        task['updated_at']=quiz.now()
        with LOCK:
            save_task(task);RUNNING.pop(task['id'],None)


def schedule(task,connection,messages):
    with LOCK:
        for identity in list(RUNNING):
            existing=load_task(identity)
            if existing['kind']==task['kind'] and ((task['kind']=='analysis' and existing.get('quiz_id')==task.get('quiz_id')) or (task['kind']=='generate' and existing.get('source_job_id')==task.get('source_job_id')) or (task['kind']=='test' and existing['profile_id']==task['profile_id'])):
                return public_task(existing)
        if len(RUNNING)>=2:raise HTTPException(409,'已有两个 AI 调用进行中，请稍后再试。')
        save_task(task);RUNNING[task['id']]=asyncio.create_task(run_task(task,connection,messages))
    return public_task(task)


@router.post('/courses/{job_id}/generate')
async def generate(job_id: str, body: GenerateBody):
    from exports import study_prompt
    import app_settings
    target=app_settings.binding(app_settings.read()['models']['generate'],'generate') if not body.profile_id else {'profile_id':body.profile_id}
    if not target.get('profile_id'):raise HTTPException(422,'请先为 AI 出题分配模型连接')
    connection=profile(target['profile_id'])
    if 'chat' not in connection.get('capabilities',['chat']):raise HTTPException(422,'此连接未启用文字对话用途，请选择合适的出题或分析模型。')
    job=quiz.source_resolver(job_id)
    if job.get('kind')!='transcribe' or job.get('status')!='completed':raise HTTPException(409,'课程转写完成后再调用模型出题。')
    source=quiz.capture_source(job_id)
    if not source['text'].strip():raise HTTPException(422,'课程正文为空，请先完成转写与校对。')
    job['title']=source['title']
    prompt=study_prompt(job)
    if len(prompt)>800000:raise HTTPException(422,'课程资料过长，请拆分课程后再出题；没有自动截断资料。')
    system=app_settings.prompt('generation_system')
    prompt+=f'\n本次优先要求：共 {body.count} 道题。'+body.instructions
    task=prepared_task('generate',connection,source_job_id=job_id,source=source,requested_count=body.count)
    return schedule(task,connection,[{'role':'system','content':system},{'role':'user','content':prompt}])


@router.post('/quizzes/{quiz_id}/analyze')
async def analyze(quiz_id: str, body: AnalyzeBody):
    import app_settings
    target=app_settings.binding(app_settings.read()['models']['analysis'],'analysis') if not body.profile_id else {'profile_id':body.profile_id}
    if not target.get('profile_id'):raise HTTPException(422,'请先为 AI 分析分配模型连接')
    connection=profile(target['profile_id'])
    if 'chat' not in connection.get('capabilities',['chat']):raise HTTPException(422,'此连接未启用文字对话用途，请选择合适的出题或分析模型。')
    with quiz.LOCK:record=copy.deepcopy(quiz.load(quiz_id))
    if record['revision']!=body.revision:raise HTTPException(409,'作答已更新，请重新打开题集后再分析。')
    if not record.get('result'):raise HTTPException(409,'请先提交作答并判分，再让 AI 分析。')
    prompt=quiz.export_record(record,'responses')+'\n本次补充要求：'+body.instructions
    if len(prompt)>800000:raise HTTPException(422,'分析资料过长，请拆分题集或课程。没有自动截断作答。')
    task=prepared_task('analysis',connection,source_job_id=record.get('source_job_id'),quiz_id=quiz_id,input_revision=record['revision'],input_answers=record['answers'])
    return schedule(task,connection,[{'role':'system','content':app_settings.prompt('analysis_system')}, {'role':'user','content':prompt}])


@router.post('/test')
async def test_connection(body: TestBody):
    connection=profile(body.profile_id);task=prepared_task('test',connection)
    return schedule(task,connection,[{'role':'user','content':'连接测试。请只回复 OK。'}])


@router.get('/tasks')
def list_tasks():
    with LOCK:
        tasks=[]
        for path in TASKS_DIR.glob('*.json'):
            try:tasks.append(public_task(json.loads(path.read_text(encoding='utf-8'))))
            except (ValueError,OSError):continue
        return sorted(tasks,key=lambda t:t['created_at'],reverse=True)[:100]


@router.get('/tasks/{identity}')
def get_task(identity: str):
    with LOCK:return public_task(load_task(identity))


@router.get('/tasks/{identity}/output')
def get_output(identity: str):
    with LOCK:task=load_task(identity)
    return Response(task.get('raw_output',''),media_type='text/plain; charset=utf-8')


@router.post('/tasks/{identity}/cancel')
async def cancel_task(identity: str):
    with LOCK:
        task=load_task(identity);running=RUNNING.get(identity)
        if running:running.cancel()
    if running:
        await asyncio.gather(running,return_exceptions=True)
        with LOCK:
            current=load_task(identity)
            if current['status'] in {'queued','running'}:
                current.update(status='cancelled',message='本地请求已取消。',updated_at=quiz.now());save_task(current)
            RUNNING.pop(identity,None)
    return get_task(identity)


async def startup():
    # Never silently replay billable requests after restarting the local app.
    with LOCK:
        for path in TASKS_DIR.glob('*.json'):
            try:
                task=json.loads(path.read_text(encoding='utf-8'))
                if task.get('status') in {'queued','running'}:
                    task.update(status='failed',message='上次运行已中断，没有自动重试。供应商是否已计费请查看其账单。',updated_at=quiz.now());save_task(task)
            except (ValueError,OSError):continue


async def shutdown():
    with LOCK:running=list(RUNNING.values())
    for task in running:task.cancel()
    if running:await asyncio.gather(*running,return_exceptions=True)
