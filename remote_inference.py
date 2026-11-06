"""Use LiteLLM's transcription and vision adapters from the isolated job process."""
import asyncio
import base64
from io import BytesIO
import wave
from fastapi import HTTPException
import ai_service


class RemoteSession:
    def __enter__(self):
        self.runner=asyncio.Runner();return self

    def __exit__(self,*args):
        self.runner.close()

    def connection(self,binding,capability):
        profile=ai_service.profile(binding['profile_id'])
        if capability not in profile.get('capabilities',['chat']):raise HTTPException(422,'所选连接未声明此功能用途，请在模型供应商设置中选择合适的模型。')
        return profile

    async def speech_call(self,audio,binding,job):
        import numpy as np
        connection=self.connection(binding,'transcription')
        from urllib.parse import urlsplit
        no_key_local=connection.get('base_url') and ai_service.local_address(urlsplit(connection['base_url']).hostname)
        if not connection.get('api_key') and not no_key_local and not connection.get('provider_params'):
            raise HTTPException(422,'此语音连接没有保存 API Key 或供应商认证参数。')
        client=await asyncio.to_thread(ai_service.sdk)
        data=BytesIO()
        with wave.open(data,'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
            output.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
        data.seek(0);data.name='lesson-segment.wav'
        kwargs=dict(connection.get('provider_params',{}))
        # These belong to chat completion, not the audio transcription endpoint.
        for key in ('response_format','max_tokens','max_completion_tokens','temperature'):kwargs.pop(key,None)
        kwargs.update(model=connection['model'],file=data,custom_llm_provider=connection['provider'],
                      timeout=connection['timeout'],num_retries=0,max_retries=0)
        if connection.get('api_key'):kwargs['api_key']=connection['api_key']
        elif no_key_local:kwargs['api_key']='local-no-key'
        if connection.get('base_url'):kwargs['api_base']=connection['base_url']
        if job['language'] not in {'mixed','auto'}:kwargs['language']=job['language']
        kwargs['prompt']=job.get('prompt_snapshot',{}).get('speech','')+(('\n课程术语：'+job['vocabulary']) if job.get('vocabulary') else '')
        result=await asyncio.wait_for(client.atranscription(**kwargs),timeout=connection['timeout']+5)
        text=getattr(result,'text',None)
        if not isinstance(text,str):raise HTTPException(422,'语音模型没有返回文字，请确认该连接支持语音转写接口。')
        return text.strip(),connection['name']+' / '+connection['model']

    def transcribe(self,audio,binding,job):
        try:return self.runner.run(self.speech_call(audio,binding,job))
        except Exception as error:raise RuntimeError(ai_service.safe_error(error)) from None

    async def vision_call(self,path,binding,job):
        connection=self.connection(binding,'vision')
        mime='image/png' if path.suffix.lower()=='.png' else 'image/webp' if path.suffix.lower()=='.webp' else 'image/jpeg'
        url='data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode('ascii')
        response=await ai_service.model_call(connection,[{'role':'user','content':[{'type':'text','text':job.get('prompt_snapshot',{}).get('vision','请提取图片文字。')},{'type':'image_url','image_url':{'url':url}}]}])
        if response.get('finish_reason') in {'length','max_tokens'}:raise HTTPException(422,'画面识别输出达到模型上限，请提高输出预算后重试。')
        return [{'text':line.strip(),'confidence':None,'box':None} for line in response['text'].splitlines() if line.strip()]

    def recognize(self,path,binding,job):
        try:return self.runner.run(self.vision_call(path,binding,job))
        except Exception as error:raise RuntimeError(ai_service.safe_error(error)) from None
