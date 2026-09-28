"""Native Feishu Sheets destination with guarded appends and cloud transcript links."""
import datetime,json,re,subprocess
from pathlib import Path
from workflow import Workflow,load,save,video_id
from excel_workflow import ExcelWorkflow
HEADERS=['博主','发布时间','选题','原标题','Tag','点赞数','收藏数','评论数','分享数','口播TXT','字幕SRT','原链接','采集状态','选用状态','时长秒','平台']

class SheetsWorkflow(ExcelWorkflow):
    def __init__(self,config):
        Workflow.__init__(self,config)
        # The inherited metadata builder stages TXT/SRT here; the workbook is only a snapshot.
        self.xlsx=Path(self.cfg['excel_path'])
        self.assets_path=Path(self.cfg['cloud_transcripts_path'])
        self.assets=load(self.assets_path) if self.assets_path.exists() else {}
        self.sheet_args=['--spreadsheet-token',self.cfg['spreadsheet_token'],'--sheet-id',self.cfg['sheet_id']]
    def api(self,group,command,args=(),sheet=True):
        argv=['lark-cli',group,command]+(self.sheet_args if sheet else [])+list(args)+['--as','user']
        p=subprocess.run(argv,cwd=self.workspace,capture_output=True,text=True,timeout=600)
        try:x=json.loads(p.stdout)
        except Exception:raise RuntimeError('INVALID_CLI_OUTPUT: '+p.stderr[:500])
        if p.returncode or not x.get('ok'):
            save(self.work/'last-sheets-error.json',{'command':command,'stdout':p.stdout,'stderr':p.stderr})
            raise RuntimeError('SHEETS_CLI_FAILED: '+command)
        return x['data']
    def schema(self):
        info=self.api('sheets','+workbook-info',['--spreadsheet-token',self.cfg['spreadsheet_token']],sheet=False)
        if not any(s['sheet_id']==self.cfg['sheet_id'] and not s.get('is_hidden') for s in info['sheets']):raise RuntimeError('SHEET_MISSING')
        self.store('read')
    def table(self):
        path=self.work/'sheets-readback.json'
        receipt=self.api('sheets','+table-get',['--output-path',str(path.relative_to(self.workspace))])
        if not receipt.get('complete') or receipt.get('truncated'):raise RuntimeError('INCOMPLETE_SHEET_READ')
        x=load(path)['sheets']
        if len(x)!=1 or x[0]['columns']!=HEADERS or not x[0]['range'].startswith('A1:'):raise RuntimeError('SCHEMA_CHANGED; preserve user changes, do not rebuild')
        self.current=x[0]
        return [{**dict(zip(HEADERS,r)),'_row':i+2} for i,r in enumerate(x[0]['data']) if any(v not in (None,'') for v in r)]
    def upload(self,row,key):
        vid=video_id(row['原链接']);source=self.xlsx.parent/row[key]
        if self.assets.get(vid,{}).get(key):return self.assets[vid][key]['url']
        x=self.api('drive','+upload',['--file',str(source.relative_to(self.workspace)),'--folder-token',self.cfg['transcript_folder_token'],'--name',row['博主']+'_'+vid+'_'+source.name],sheet=False)
        if x.get('size')!=source.stat().st_size or not x.get('url'):raise RuntimeError('UPLOAD_VERIFY_FAILED: '+vid)
        self.assets.setdefault(vid,{})[key]=x;save(self.assets_path,self.assets)
        return x['url']
    def ensure_filter(self):
        rows=self.table();end=max([r['_row'] for r in rows]+[2])
        f=self.api('sheets','+filter-list');existing=[a for s in f.get('sheets',[]) for a in s.get('filters',[])]
        if existing:
            details=existing[0]['details'];props={'rules':[{k:v for k,v in rule.items() if k!='filtered_rows'} for rule in details.get('rules',[])]}
            if details['range']==f'A1:P{end}':return
            self.api('sheets','+filter-update',['--range',f'A1:P{end}','--properties',json.dumps(props,ensure_ascii=False)])
        else:self.api('sheets','+filter-create',['--range',f'A1:P{end}'])
        verify=self.api('sheets','+filter-list')
        if verify['sheets'][0]['filters'][0]['details']['range']!=f'A1:P{end}':raise RuntimeError('FILTER_VERIFY_FAILED')
    def store(self,mode,records=None):
        if mode=='read':return {'records':self.table()}
        if mode!='append':raise RuntimeError('UNSUPPORTED_SHEETS_MODE')
        for row in records or []:
            vid=video_id(row['原链接']);links={k:self.upload(row,k) for k in ('txt','srt') if row.get(k)}
            current=self.table()
            if any(video_id(r['原链接'])==vid for r in current):continue
            dest=max([r['_row'] for r in current]+[1])+1
            # styles only; values are guarded below against concurrent user edits.
            template=2 if dest%2==0 else 3
            self.api('sheets','+range-copy',['--source-range',f'A{template}:P{template}','--target-range',f'A{dest}','--paste-type','formats'])
            layout=self.api('sheets','+sheet-info',['--range',f'A{template}:P{template}','--include','row_heights'])
            heights=layout.get('row_heights',[])
            if heights and heights[0].get('height'):
                spec={'styles':[{'name':self.current['name'],'row_sizes':[{'range':f'{dest}:{dest}','size':heights[0]['height']}]}]}
                self.api('sheets','+styles-put',['--spreadsheet-token',self.cfg['spreadsheet_token'],'--styles',json.dumps(spec)],sheet=False)
            cells=[]
            for col in HEADERS:
                value=row.get(col,'');cell={'value':value if value is not None else ''}
                if col=='发布时间':
                    dt=datetime.datetime.strptime(value,'%Y-%m-%d %H:%M:%S')
                    cell={'value':(dt-datetime.datetime(1899,12,30)).total_seconds()/86400,'cell_styles':{'number_format':'yyyy-mm-dd hh:mm'}}
                elif col in ('口播TXT','字幕SRT'):
                    key='txt' if col=='口播TXT' else 'srt'
                    if key in links:cell={'rich_text':[{'type':'link','text':'打开口播' if key=='txt' else '打开字幕','link':links[key]}]}
                elif col=='原链接':cell={'rich_text':[{'type':'link','text':value,'link':value}]}
                cells.append(cell)
            path=self.work/'sheets-append.json';save(path,[cells])
            self.api('sheets','+cells-set',['--range',f'A{dest}:P{dest}','--allow-overwrite=false','--cells','@'+str(path.relative_to(self.workspace))])
            read=self.api('sheets','+cells-get',['--range',f'A{dest}:P{dest}','--include','value'])
            if read.get('has_more'):raise RuntimeError('INCOMPLETE_APPEND_READBACK')
            actual=read['ranges'][0]['cells'][0]
            if video_id(actual[11].get('value'))!=vid:raise RuntimeError('ROW_VERIFY_FAILED')
            for index,key in ((9,'txt'),(10,'srt')):
                if key in links and not any(p.get('link')==links[key] for p in actual[index].get('rich_text',[])):raise RuntimeError('LINK_VERIFY_FAILED')
        if self.cfg.get('sort_newest_first') and records:self.sort_recent()
        self.ensure_filter()
        return {'records':self.table()}
    def sort_recent(self):
        before=self.table()
        if len(before)<2:return
        end=max(r['_row'] for r in before)
        layout=self.api('sheets','+sheet-info',['--range',f'A1:P{end}','--include','merges'])
        if layout.get('merged_cells'):raise RuntimeError('SORT_BLOCKED_BY_MERGES')
        path=self.work/'sort-before-cells.json'
        receipt=self.api('sheets','+cells-get',['--range',f'A2:P{end}','--include','value,formula,style','--output-path',str(path.relative_to(self.workspace))])
        if not receipt.get('complete'):raise RuntimeError('INCOMPLETE_SORT_PREFLIGHT')
        original=load(path)
        if any(c.get('formula') for b in original['ranges'] for row in b['cells'] for c in row):raise RuntimeError('SORT_FORMULAS_REQUIRE_REVIEW')
        self.api('sheets','+range-sort',['--range',f'A1:P{end}','--has-header','--sort-keys','[{"column":"B","ascending":false}]'])
        after=self.table()
        def values(rows):return {r['原链接']:{k:v for k,v in r.items() if k!='_row'} for r in rows}
        if values(before)!=values(after):raise RuntimeError('SORT_RECORD_VERIFY_FAILED')
        target=self.work/'sort-after-cells.json'
        receipt=self.api('sheets','+cells-get',['--range',f'A2:P{end}','--include','value,style','--output-path',str(target.relative_to(self.workspace))])
        if not receipt.get('complete'):raise RuntimeError('INCOMPLETE_SORT_READBACK')
        checked=load(target)
        def links(data):return {r[11]['value']:[r[i].get('rich_text',[]) for i in (9,10,11)] for b in data['ranges'] for r in b['cells']}
        if links(original)!=links(checked):raise RuntimeError('SORT_LINK_VERIFY_FAILED')
        times=[];styles=[]
        for block in checked['ranges']:
            for n,row in zip(block['row_indices'],block['cells']):
                times.append(row[1]['value'])
                colors={c.get('cell_styles',{}).get('background_color') for c in row}
                if len(colors)==1 and colors.issubset({'#FFFFFF','#F3F6FA'}):
                    styles.append({'range':f'A{n}:P{n}','background_color':'#F3F6FA' if n%2==0 else '#FFFFFF'})
        if times!=sorted(times,reverse=True):raise RuntimeError('SORT_DATE_VERIFY_FAILED')
        if styles:self.api('sheets','+styles-put',['--spreadsheet-token',self.cfg['spreadsheet_token'],'--styles',json.dumps({'styles':[{'name':self.current['name'],'cell_styles':styles}]})],sheet=False)

    def records(self):
        rows=super().records()
        # Pending retries must not mark a partially written row complete.
        for vid,r in rows.items():
            if vid not in self.state['completed'] and '未识别到口播' not in r.get('采集状态',''):
                if not (r.get('口播TXT') and r.get('字幕SRT')):raise RuntimeError('INCOMPLETE_EXISTING_ROW: '+vid)
                n=r['_row'];x=self.api('sheets','+cells-get',['--range',f'J{n}:K{n}','--include','value'])
                if x.get('has_more') or not all(any(p.get('link','').startswith('https://') for p in c.get('rich_text',[])) for c in x['ranges'][0]['cells'][0]):raise RuntimeError('INCOMPLETE_EXISTING_LINKS: '+vid)
        return rows
    def commit(self,batch_path,enrichment_path):
        # Restores filter coverage after an interrupted append without changing user rules.
        self.schema();self.ensure_filter()
        # An interrupted append may already exist in the cloud but lack completion state.
        batch=load(batch_path)
        if self.cfg.get('sort_newest_first') and batch.get('items'):
            cloud=self.records()
            if any(b['id'] in cloud and b['id'] not in self.state['completed'] for b in batch['items']):self.sort_recent()
        return super().commit(batch_path,enrichment_path)
