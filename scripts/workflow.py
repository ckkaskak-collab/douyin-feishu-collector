"""Prepare and commit a resumable daily batch using current Feishu schema."""
import argparse, datetime, fcntl, json, os, re, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent

def load(p): return json.loads(Path(p).read_text())
def save(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(x,ensure_ascii=False,indent=2));t.replace(p)
def video_id(value):
    match=re.search(r'(?:/video/|modal_id=)(\d+)', str(value or ''))
    return match.group(1) if match else None

def title_without_tags(description, tags=()):
    """Keep caption prose; remove known hashtags and an explicit trailing tag block."""
    text=description or ''
    names=sorted({t for t in tags if t},key=len,reverse=True)
    if names:
        pattern=r'(?<![A-Za-z0-9_/#])#(?:'+'|'.join(re.escape(t) for t in names)+r')(?!\w)'
        parts=re.split(r'((?:https?://|www\.)\S+)',text)
        text=''.join(part if i%2 else re.sub(pattern,'',part,flags=re.IGNORECASE) for i,part in enumerate(parts))
    # Some visible caption tags are absent from text_extra. Only infer removal
    # for a whitespace-delimited trailing block; never infer entries for Tag.
    text=re.sub(r'(?:^|(?<=\s))#\w+(?:\s*#\w+)*\s*$', '', text)
    if text==description:return text
    return '\n'.join(line.rstrip() for line in text.splitlines()).strip()

def original_title(raw):
    return title_without_tags(raw.get('desc',''),
                              [e['hashtag_name'] for e in raw.get('text_extra',[]) if e.get('hashtag_name')])

def missing_files(existing, paths):
    missing=[]
    for path in paths:
        matches=[x for x in existing if x.get('name')==path.name]
        if matches and not any(x.get('size')==path.stat().st_size for x in matches):
            raise RuntimeError('ATTACHMENT_NAME_CONFLICT: '+path.name)
        if not matches:missing.append(path)
    return missing

class Workflow:
    def __init__(self, config):
        self.config_path=Path(config).resolve();self.cfg=load(config)
        self.workspace=Path(self.cfg['workspace']);self.work=Path(self.cfg['work_dir']);self.output=Path(self.cfg['output_dir'])
        self.work.mkdir(parents=True,exist_ok=True);self.output.mkdir(parents=True,exist_ok=True)
        self.state_path=self.work/'state.json';self.state=load(self.state_path) if self.state_path.exists() else {'version':1,'creators':{},'completed':{}}
        self.common=['--base-token',self.cfg['base_token'],'--table-id',self.cfg['table_id'],'--as','user']
    def cli(self, command, args=(), artifact=False):
        p=subprocess.run(['lark-cli','base',command]+self.common+list(args),cwd=self.workspace,capture_output=True,text=True,timeout=600)
        if p.returncode:
            save(self.work/'last-cli-error.json',{'command':command,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
            raise RuntimeError(command+' failed; inspect work/博主每日采集/last-cli-error.json')
        x=json.loads(p.stdout)
        if not artifact and not x.get('ok'): raise RuntimeError(command+' did not return ok=true: '+json.dumps(x,ensure_ascii=False)[:600])
        return x
    def schema(self):
        x=self.cli('+field-list'); self.fields={f['name']:f for f in x['data']['fields']}
        for name,kind in [('原链接','text'),('原标题','text'),('博主','text'),('采集状态','text'),('口播文件','attachment')]:
            if name not in self.fields or self.fields[name]['type']!=kind: raise RuntimeError('SCHEMA_CHANGED: '+name+'; do not recreate user-deleted columns')
        save(self.work/'current-fields.json',x)
    def records(self):
        result={};offset=0
        while True:
            path=self.work/f'records-{offset}.ndjson'; rel=str(path.relative_to(self.workspace))
            args=['--field-id','原链接','--field-id','口播文件','--field-id','采集状态','--format','ndjson','--output',rel,'--overwrite','--offset',str(offset)]
            m=self.cli('+record-list',args,artifact=True)
            for line in path.read_text().splitlines():
                r=json.loads(line);vid=video_id(r.get('原链接'))
                if vid:
                    if vid in result:raise RuntimeError('DUPLICATE_SOURCE_URL: '+vid)
                    result[vid]=r
            if not m.get('has_more'):break
            count=m['records_count']
            if not count:raise RuntimeError('EMPTY_PAGE_WITH_HAS_MORE')
            offset+=count
        return result
    def prepare(self, metadata_only=False):
        self.schema();cloud=self.records()
        # A pending batch retains its raw source and transcript after interruptions.
        pending=self.work/'pending.json'
        if pending.exists() and not metadata_only:
            x=load(pending);print(json.dumps({'status':'resume_pending','batch':x['batch']},ensure_ascii=False));return
        run=self.work/('run-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        run.mkdir(parents=True)
        complete=[vid for vid,r in cloud.items() if r.get('口播文件') and '失败' not in r.get('采集状态','') and '待转写' not in r.get('采集状态','') and '待入库' not in r.get('采集状态','')]
        job={'config':self.cfg,'state':self.state,'run_dir':str(run),'cloud_complete_ids':complete,'metadata_only':metadata_only}
        save(run/'job.json',job)
        command=[str(Path(self.cfg['mediacrawler_root'])/'.venv/bin/python'),str(HERE/'collect.py'),'--job',str(run/'job.json')]
        with (run/'crawler.log').open('w') as log:
            proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
        report=load(run/'collection.json')
        batch={'collection':str(run/'collection.json'),'run_dir':str(run),'items':[],'metadata_only':metadata_only}
        for item in report['items']:
            vid=item['id'];target=self.output/'口播文件'/vid
            batch['items'].append({'id':vid,'creator':item['creator']['name'],'description':item['raw'].get('desc',''),'transcript_dir':str(target),'error':item.get('error')})
        save(run/'batch.json',batch)
        if metadata_only:
            print(json.dumps({'status':'metadata_check','new_count':len(batch['items']),'errors':report['errors'],'batch':str(run/'batch.json')},ensure_ascii=False));return
        if batch['items']: save(pending,{'batch':str(run/'batch.json')})
        save(self.work/'latest-run.json',{'batch':str(run/'batch.json')})
        if report['errors'] or proc.returncode or any(i['error'] for i in batch['items']):
            raise RuntimeError('COLLECTION_INCOMPLETE: '+str(run/'collection.json'))
        self.transcribe_batch(run/'batch.json')
    def transcribe_batch(self,batch_path):
        batch=load(batch_path);report=load(batch['collection'])
        if batch.get('metadata_only'):raise RuntimeError('METADATA_ONLY: prepare a normal batch before transcription')
        if report['errors'] or any(i.get('error') for i in report['items']):raise RuntimeError('COLLECTION_INCOMPLETE; repair or recollect batch before continuing')
        for item,b in zip(report['items'],batch['items']):
            target=Path(b['transcript_dir']);target.mkdir(parents=True,exist_ok=True)
            with (target/'transcription.log').open('a') as log:
                subprocess.run([self.cfg['asr_python'],str(HERE/'transcribe.py'),'--config',str(self.config_path),'--media',item['media'],'--out',str(target)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=7200)
        print(json.dumps({'status':'ready_for_topics' if batch['items'] else 'no_updates','new_count':len(batch['items']),'batch':str(batch_path),'items':batch['items']},ensure_ascii=False))
    def commit(self,batch_path,enrichment_path):
        self.schema();cloud=self.records();batch=load(batch_path);report=load(batch['collection']);enrichment=load(enrichment_path)
        if batch.get('metadata_only') or report['errors']:raise RuntimeError('UNCOMMITTABLE_BATCH')
        for item,b in zip(report['items'],batch['items']):
            vid=item['id'];raw=item['raw'];target=Path(b['transcript_dir'])
            if item.get('error'):raise RuntimeError('INCOMPLETE_ITEM: '+vid)
            files=[target/'口播转写_机器稿.txt',target/'口播转写_机器稿.srt']
            transcript=load(target/'transcription.json')
            no_speech=transcript.get('status')=='no_speech'
            if no_speech:
                if transcript.get('segments'):raise RuntimeError('INVALID_NO_SPEECH: '+vid)
                files=[]
            if any(not f.is_file() or not f.stat().st_size for f in files):raise RuntimeError('MISSING_TRANSCRIPT: '+vid)
            # Completed IDs are tombstones too: deleting a user row does not resurrect it.
            if vid in self.state['completed']:continue
            topic=enrichment.get(vid,{}).get('选题','').strip()
            if '选题' in self.fields and not topic:raise RuntimeError('MISSING_TOPIC: '+vid)
            stats=raw.get('statistics') or {};video=raw.get('video') or {}
            tags=list(dict.fromkeys(e['hashtag_name'] for e in raw.get('text_extra',[]) if e.get('hashtag_name')))
            fields={'选题':topic,'原标题':original_title(raw),'Tag':' '.join('#'+t for t in tags),'平台':'抖音','博主':item['creator']['name'],
                '原链接':'https://www.douyin.com/video/'+vid,'发布时间':datetime.datetime.fromtimestamp(raw['create_time'],datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
                '采集状态':'未识别到口播，待人工核对' if no_speech else '口播已转写，附件待入库；未逐句校对','时长秒':round((video.get('duration') or raw.get('duration',0))/1000,2),
                '点赞数':stats.get('digg_count'),'评论数':stats.get('comment_count'),'收藏数':stats.get('collect_count'),'分享数':stats.get('share_count')}
            fields={k:v for k,v in fields.items() if k in self.fields}
            if '选用状态' in self.fields and any(o.get('name')=='待筛选' for o in self.fields['选用状态'].get('options',[])):fields['选用状态']=['待筛选']
            if vid not in cloud:
                payload=Path(batch['run_dir'])/(vid+'-create.json');save(payload,{'create_records':[fields]})
                r=self.cli('+record-batch-create',['--json','@'+str(payload.relative_to(self.workspace))])
                rid=r['data']['record_id_list'][0];cloud[vid]={'record_id':rid,'口播文件':[]}
                save(self.work/'last-created.json',{'id':vid,'record_id':rid})
            # Existing rows, including user edits, are preserved on resume.
            rid=cloud[vid]['record_id'];missing=missing_files(cloud[vid].get('口播文件',[]),files)
            if missing:
                args=['--record-id',rid,'--field-id','口播文件']
                for f in missing:args+=['--file',str(f.relative_to(self.workspace))]
                self.cli('+record-upload-attachment',args)
            status={'update_records':{rid:{'采集状态':'未识别到口播，待人工核对' if no_speech else '口播已转写，待校对'}}}
            self.cli('+record-batch-update',['--json',json.dumps(status,ensure_ascii=False)])
            # Read back attachment metadata before marking complete or deleting temp video.
            cloud=self.records();r=cloud.get(vid)
            if not r or missing_files(r.get('口播文件',[]),files):raise RuntimeError('VERIFY_ATTACHMENTS_FAILED: '+vid)
            media=Path(item['media']).resolve();temp=(Path(batch['run_dir'])/'temporary').resolve()
            if not no_speech and media.is_file() and temp in media.parents:
                if self.cfg.get('retain_video'):
                    import shutil
                    saved=self.output/'视频'/vid/media.name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(media),str(saved))
                else:media.unlink()
            self.state['completed'][vid]={'record_id':rid,'creator':item['creator']['sec_user_id'],'transcription_status':'no_speech' if no_speech else 'machine_transcribed'};save(self.state_path,self.state)
        for sid,data in report['creators'].items():
            self.state['creators'][sid]={'since':data['latest']}
        save(self.state_path,self.state)
        review_ids=[b['id'] for b in batch['items'] if load(Path(b['transcript_dir'])/'transcription.json').get('status')=='no_speech']
        summary={'status':'complete_with_review' if review_ids else 'complete','count':len(batch['items']),'transcribed_count':len(batch['items'])-len(review_ids),'review_required_ids':review_ids,'base_url':self.cfg['base_url'],'completed_at':datetime.datetime.now().astimezone().isoformat()}
        save(Path(batch['run_dir'])/'result.json',summary)
        pending=self.work/'pending.json'
        if pending.exists() and Path(load(pending)['batch']).resolve()==Path(batch_path).resolve():pending.unlink()
        print(json.dumps(summary,ensure_ascii=False))

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('stage',choices=['check','prepare','transcribe','commit']);p.add_argument('--metadata-only',action='store_true');p.add_argument('--batch');p.add_argument('--enrichment');a=p.parse_args()
    if load(a.config).get('destination')=='sheets':
        from sheets_workflow import SheetsWorkflow
        w=SheetsWorkflow(a.config)
    elif load(a.config).get('destination')=='excel':
        from excel_workflow import ExcelWorkflow
        w=ExcelWorkflow(a.config)
    else:w=Workflow(a.config)
    with (w.work/'workflow.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('ANOTHER_RUN_ACTIVE')
        if a.stage=='check':
            for path in [Path(w.cfg['mediacrawler_root'])/'.venv/bin/python',Path(w.cfg['asr_python']),Path(w.cfg['ffmpeg'])]:
                if not path.exists():raise RuntimeError('RUNTIME_MISSING: '+str(path))
            w.schema();r=w.records();print(json.dumps({'status':'ready','creators':len([c for c in w.cfg['creators'] if c.get('enabled',True)]),'cloud_records':len(r),'local_completed':len(w.state['completed'])}))
        elif a.stage=='prepare':w.prepare(a.metadata_only)
        elif a.stage=='transcribe':w.transcribe_batch(a.batch)
        elif a.stage=='commit':
            if not a.batch or not a.enrichment:p.error('commit requires --batch and --enrichment')
            w.commit(a.batch,a.enrichment)
if __name__=='__main__':main()
