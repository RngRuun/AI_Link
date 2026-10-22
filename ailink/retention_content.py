"""Deterministic, bounded preservation of useful local conversation content."""
import hashlib
import json
import re
import stat
from pathlib import Path
from urllib.parse import unquote

MAX_TRANSCRIPT=64*1024*1024
ARTIFACT_EXTENSIONS={'.pdf','.docx','.xlsx','.pptx','.csv','.tsv','.json','.md','.txt','.png','.jpg','.jpeg','.webp','.svg','.html','.py','.js','.ts','.vue','.zip'}

def safe_file(path, root, limit=MAX_TRANSCRIPT):
    path,root=Path(path).absolute(),Path(root).absolute()
    if root.resolve()!=root:raise ValueError('数据根目录不能经过链接或重解析目录。')
    if not path.is_relative_to(root) or path==root:raise ValueError('文件不在允许的数据目录内。')
    for entry in [path,*path.parents]:
        if not entry.exists():raise ValueError('文件或目录已不存在。')
        info=entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('不处理链接或重解析目录。')
        if entry==root:break
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>limit:raise ValueError('文件类型、硬链接或大小超出清理限制。')
    return path

def fingerprint(path):
    info=path.stat()
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    after=path.stat()
    if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('记录正在变化，请稍后重新预览。')
    return {'bytes':info.st_size,'mtime':info.st_mtime_ns,'sha256':digest}

def local_content(path):
    final=[];fallback=[];count=0
    with path.open(encoding='utf-8-sig') as stream:
        for line in stream:
            if len(line)>2*1024*1024:raise ValueError('记录单行超过 2 MiB，未清理。')
            value=json.loads(line)
            if not isinstance(value,dict):raise ValueError('会话记录必须是 JSON 对象。')
            payload=value.get('payload') or {}
            if not isinstance(payload,dict):raise ValueError('会话载荷必须是 JSON 对象。')
            text='';phase=payload.get('phase')
            if value.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role')=='assistant':
                text='\n'.join(c.get('text','') for c in payload.get('content',[]) if isinstance(c,dict) and isinstance(c.get('text'),str))
            elif value.get('type')=='event_msg' and payload.get('type')=='agent_message':text=payload.get('message','')
            if isinstance(text,str) and text.strip():
                count+=len(text)
                if count>2*1024*1024:raise ValueError('助手正文超过 2 MiB，需分批人工检查。')
                fallback.append(text)
                if phase=='final_answer':final.append(text)
    return classify(final or fallback[-1:],fallback)

def classify(final, all_text=None):
    final=list(dict.fromkeys(final));texts=all_text or final
    decisions=[];codes=[]
    for text in final:
        codes.extend(re.findall(r'```[^\n]*\n[\s\S]*?```',text))
        blocks=re.split(r'\n\s*\n',text)
        decisions.extend(block for block in blocks if re.search(r'结论|决定|约束|验证|测试|待办|下一步|decision|constraint|verified|next step',block,re.I))
    links=[]
    for text in texts:
        for label,target in re.findall(r'\[([^\]]{1,160})\]\(([^)\n]{1,2000})\)',text):
            target=unquote(target.strip().strip('<>'))
            if not re.match(r'https?://',target,re.I):links.append({'name':label,'path':target})
    return {'final':'\n\n'.join(final),'decisions':'\n\n'.join(dict.fromkeys(decisions)),
            'code':'\n\n'.join(dict.fromkeys(codes)),'files':list({v['path']:v for v in links}.values())[:200]}

def write_preserved(directory, content, keep, cwd=None):
    """Never delete project files; copy only safe, referenced deliverables."""
    directory.mkdir(parents=True,exist_ok=True);files=[];total=0
    for key,title in [('final','最终答复'),('decisions','重要结论'),('code','代码片段')]:
        if keep[key] and content[key]:(directory/(key+'.md')).write_text('# '+title+'\n\n'+content[key],encoding='utf-8')
    if keep['files']:
        for link in content['files']:
            item={**link,'status':'仅索引，原文件保留原位'}
            try:
                if not cwd or not Path(cwd).is_absolute():raise ValueError()
                raw=re.sub(r':\d+(?::\d+)?$','',link['path'].removeprefix('file://'))
                raw=re.sub(r'^/+([a-zA-Z]:[\\/])',r'\1',raw)
                target=Path(raw)
                if not target.is_absolute():target=Path(cwd)/target
                if target.suffix.lower() not in ARTIFACT_EXTENSIONS:raise ValueError()
                if target.name.casefold() in ('auth.json','credentials.json','token.json','tokens.json','config.json'):raise ValueError()
                target=safe_file(target,Path(cwd),25*1024*1024)
                if any(p.startswith('.') for p in target.relative_to(cwd).parts):raise ValueError()
                if total+target.stat().st_size>100*1024*1024:raise ValueError()
                before=fingerprint(target);data=target.read_bytes()
                if hashlib.sha256(data).hexdigest()!=before['sha256']:raise ValueError()
                name=before['sha256'][:12]+'-'+target.name
                (directory/'files').mkdir(exist_ok=True);(directory/'files'/name).write_bytes(data)
                item.update(status='已复制保留',saved='files/'+name);total+=len(data)
            except (ValueError,OSError):pass
            files.append(item)
        (directory/'files.json').write_text(json.dumps(files,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'files':len(files),'copied':sum(f.get('saved') is not None for f in files),'final':bool(keep['final'] and content['final']),
            'decisions':bool(keep['decisions'] and content['decisions']),'code':bool(keep['code'] and content['code'])}
