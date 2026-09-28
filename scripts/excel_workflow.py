"""Local Excel destination, preserving existing rows and user selections."""
import datetime,json,re,shutil,subprocess
from pathlib import Path
from workflow import Workflow,load,save,video_id,HERE,original_title

class ExcelWorkflow(Workflow):
    def __init__(self,config):
        super().__init__(config)
        self.xlsx=Path(self.cfg['excel_path'])
        self.runtime=self.work/'excel-runtime';self.runtime.mkdir(exist_ok=True)
        modules=self.runtime/'node_modules'
        if not modules.exists():modules.symlink_to(self.cfg['node_modules'],target_is_directory=True)
        shutil.copy2(HERE/'excel_store.mjs',self.runtime/'excel_store.mjs')
        shutil.copy2(HERE/'xlsx_links.py',self.runtime/'xlsx_links.py')
    def store(self,mode,records=None):
        report=self.work/'excel-readback.json';job=self.work/'excel-job.json'
        save(job,{'xlsx':str(self.xlsx),'records':records or [],'report':str(report),'backupDir':str(self.work/'excel-backups'),'python':self.cfg['spreadsheet_python']})
        with (self.work/'excel-store.log').open('a') as log:
            subprocess.run([self.cfg['node'],str(self.runtime/'excel_store.mjs'),mode,str(job)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600,cwd=self.workspace)
        return load(report)
    def schema(self):
        if not self.xlsx.is_file():raise RuntimeError('EXCEL_MISSING: '+str(self.xlsx))
        if self.xlsx.with_name('~$'+self.xlsx.name).exists():raise RuntimeError('EXCEL_OPEN: close workbook before updating')
    def records(self):
        result={}
        for row in self.store('read')['records']:
            vid=video_id(row.get('原链接'))
            if not vid:raise RuntimeError('EXCEL_SOURCE_URL_MISSING')
            if vid in result:raise RuntimeError('EXCEL_DUPLICATE_SOURCE_URL: '+vid)
            result[vid]={**row,'口播文件':bool(row.get('口播TXT'))}
        return result
    def commit(self,batch_path,enrichment_path):
        self.schema();cloud=self.records();batch=load(batch_path);report=load(batch['collection']);topics=load(enrichment_path)
        if batch.get('metadata_only') or report['errors']:raise RuntimeError('UNCOMMITTABLE_BATCH')
        incoming=[];processed=[];review=[]
        for item,b in zip(report['items'],batch['items']):
            vid=item['id']
            if item.get('error'):raise RuntimeError('INCOMPLETE_ITEM: '+vid)
            if vid in self.state['completed']:continue
            raw=item['raw'];target=Path(b['transcript_dir']);transcript=load(target/'transcription.json')
            no_speech=transcript.get('status')=='no_speech'
            if no_speech and transcript.get('segments'):raise RuntimeError('INVALID_NO_SPEECH: '+vid)
            topic=topics.get(vid,{}).get('选题','').strip()
            if not topic:raise RuntimeError('MISSING_TOPIC: '+vid)
            stats=raw.get('statistics') or {};video=raw.get('video') or {}
            row={'博主':item['creator']['name'],'发布时间':datetime.datetime.fromtimestamp(raw['create_time'],datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
                 '选题':topic,'原标题':original_title(raw),'Tag':' '.join('#'+s for s in dict.fromkeys(e['hashtag_name'] for e in raw.get('text_extra',[]) if e.get('hashtag_name'))),
                 '平台':'抖音','原链接':'https://www.douyin.com/video/'+vid,'时长秒':round((video.get('duration') or raw.get('duration',0))/1000,2),
                 '点赞数':stats.get('digg_count'),'收藏数':stats.get('collect_count'),'评论数':stats.get('comment_count'),'分享数':stats.get('share_count'),
                 '采集状态':'未识别到口播，待人工核对' if no_speech else '口播已转写，待校对','选用状态':'待筛选'}
            if no_speech:review.append(vid)
            else:
                for ext,key,name in [('txt','txt','口播.txt'),('srt','srt','字幕.srt')]:
                    source=target/('口播转写_机器稿.'+ext)
                    if not source.is_file() or not source.stat().st_size:raise RuntimeError('MISSING_TRANSCRIPT: '+vid)
                    dest=self.xlsx.parent/'口播文件'/vid/name;dest.parent.mkdir(parents=True,exist_ok=True)
                    if dest.exists() and dest.read_bytes()!=source.read_bytes():raise RuntimeError('TRANSCRIPT_CONFLICT: '+str(dest))
                    if not dest.exists():shutil.copy2(source,dest)
                    row[key]=dest.relative_to(self.xlsx.parent).as_posix()
            if vid not in cloud:incoming.append(row)
            processed.append((item,no_speech))
        if incoming:
            result=self.store('append',incoming)
            ids={video_id(r.get('原链接')) for r in result['records']}
            if any(i['id'] not in ids for i,_ in processed):raise RuntimeError('EXCEL_VERIFY_FAILED')
        for item,no_speech in processed:
            vid=item['id'];media=Path(item['media']).resolve();temp=(Path(batch['run_dir'])/'temporary').resolve()
            self.state['completed'][vid]={'destination':self.cfg.get('destination','excel'),'creator':item['creator']['sec_user_id'],'transcription_status':'no_speech' if no_speech else 'machine_transcribed'}
            save(self.state_path,self.state)
            if not no_speech and media.is_file() and temp in media.parents:
                if self.cfg.get('retain_video'):
                    dest=self.output/'视频'/vid/media.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(media),str(dest))
                else:media.unlink()
        for sid,data in report['creators'].items():self.state['creators'][sid]={'since':data['latest']}
        save(self.state_path,self.state)
        result={'status':'complete_with_review' if review else 'complete','count':len(incoming),'review_required_ids':review,'destination':self.cfg.get('destination','excel'),'url':self.cfg.get('sheets_url',str(self.xlsx)),'completed_at':datetime.datetime.now().astimezone().isoformat()}
        save(Path(batch['run_dir'])/'result-excel.json',result)
        pending=self.work/'pending.json'
        if pending.exists() and Path(load(pending)['batch']).resolve()==Path(batch_path).resolve():pending.unlink()
        print(json.dumps(result,ensure_ascii=False))
