import sys,json,tempfile,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from sheets_workflow import SheetsWorkflow,HEADERS
from workflow import load
class Fake(SheetsWorkflow):
 def __init__(self,work):
  self.work=work;self.workspace=work;self.cfg={'spreadsheet_token':'test'};self.current={'name':'内容库'};self.rows=[dict(zip(HEADERS,['用户改过的博主','','保留用户选题','','',1,2,3,4,'','','https://www.douyin.com/video/1','','已选用',0,'抖音']),_row=2)];self.calls=[]
 def table(self):return self.rows
 def upload(self,row,key):return 'https://example.invalid/'+key
 def ensure_filter(self):self.filter_end=max(r['_row'] for r in self.rows)
 def api(self,group,command,args=(),sheet=True):
  self.calls.append((command,args));a=list(args)
  if command=='+range-copy':assert a[a.index('--paste-type')+1]=='formats';return {}
  if command=='+sheet-info':return {'row_heights':[{'height':64}]}
  if command=='+styles-put':
   spec=json.loads(a[a.index('--styles')+1]);assert spec['styles'][0]['row_sizes'][0]['size']==64;return {}
  if command=='+cells-set':
   assert '--allow-overwrite=false' in a
   self.cells=load(self.work/'sheets-append.json')[0]
   assert isinstance(self.cells[1]['value'],float)
   assert self.cells[1]['cell_styles']['number_format']=='yyyy-mm-dd hh:mm'
   assert self.cells[9]['rich_text'][0]['link'].endswith('/txt')
   assert self.cells[10]['rich_text'][0]['link'].endswith('/srt')
   self.actual=[dict(c,value=c.get('value',c.get('rich_text',[{}])[0].get('text',''))) for c in self.cells]
   self.rows.append(dict(zip(HEADERS,[c['value'] for c in self.actual]),_row=3));return {}
  if command=='+cells-get':return {'has_more':False,'ranges':[{'cells':[self.actual]}]}
  raise AssertionError(command)
with tempfile.TemporaryDirectory() as t:
 f=Fake(Path(t));source={'博主':'新博主','发布时间':'2026-09-27 12:36:16','原链接':'https://www.douyin.com/video/2','txt':'a.txt','srt':'a.srt','选题':'新选题','选用状态':'待筛选'}
 first=f.store('append',[source]);second=f.store('append',[source]);assert len(f.rows)==2 and f.rows[0]['选题']=='保留用户选题' and f.rows[0]['选用状态']=='已选用';assert f.filter_end==3;assert len([x for x in f.calls if x[0]=='+cells-set'])==1
 assert abs(f.cells[1]['value']-46292.52518518519)<1e-8
 print('PASS: one guarded append, real date/time, cloud links, idempotency, preserved edits, expanded range')
