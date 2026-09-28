"""Offline behavioral checks. Never connects to Douyin or Feishu."""
import json, tempfile, unittest
from pathlib import Path
from collect import select_recent
from workflow import Workflow, load, save, video_id, missing_files, title_without_tags

class FakeWorkflow(Workflow):
    def __init__(self, config):
        super().__init__(config);self.cloud={};self.calls=[];self.fail_upload=False
    def schema(self):
        self.fields={x:{'type':'text'} for x in ['选题','原标题','Tag','平台','博主','原链接','发布时间','采集状态','时长秒','点赞数','收藏数','评论数','分享数']}
        self.fields['口播文件']={'type':'attachment'}
    def records(self):return json.loads(json.dumps(self.cloud))
    def cli(self, cmd, args=(), artifact=False):
        self.calls.append(cmd);args=list(args)
        if cmd=='+record-batch-create':
            payload=load(self.workspace/args[args.index('--json')+1][1:]);r=payload['create_records'][0];vid=video_id(r['原链接'])
            self.cloud[vid]={'record_id':'rec-test','口播文件':[],**r};return {'ok':True,'data':{'record_id_list':['rec-test']}}
        if cmd=='+record-upload-attachment':
            if self.fail_upload:raise RuntimeError('temporary upload failure')
            row=next(r for r in self.cloud.values() if r['record_id']==args[args.index('--record-id')+1])
            for i,arg in enumerate(args):
                if arg=='--file':
                    f=self.workspace/args[i+1];row['口播文件'].append({'name':f.name,'size':f.stat().st_size,'file_token':'fake-token'})
            return {'ok':True}
        if cmd=='+record-batch-update':
            updates=json.loads(args[args.index('--json')+1])['update_records']
            for row in self.cloud.values():row.update(updates.get(row['record_id'],{}))
            return {'ok':True}
        raise AssertionError('Unexpected mutation '+cmd)

class Invariants(unittest.TestCase):
    def test_title_removes_tags_without_losing_prose(self):
        self.assertEqual(title_without_tags('正文#AI教学 #Agent', ['ai教学','agent']), '正文')
        self.assertEqual(title_without_tags('正文\n#BrowserSkill #AI工具', ['AI工具']), '正文')
        self.assertEqual(title_without_tags('正文 #AI设计 后续正文', ['AI']), '正文 #AI设计 后续正文')
        self.assertEqual(title_without_tags('C# 教程 https://example.com/#Agent #AI', ['Agent','AI']), 'C# 教程 https://example.com/#Agent')
        self.assertEqual(title_without_tags('C#AI https://example.com/中文#Agent #AI', ['Agent','AI']), 'C#AI https://example.com/中文#Agent')
        self.assertEqual(title_without_tags('正文\n第二段 @作者 #AI', ['AI']), '正文\n第二段 @作者')
        self.assertEqual(title_without_tags('#AI #Agent', ['AI','Agent']), '')
        self.assertEqual(title_without_tags('正文  保持格式'), '正文  保持格式')
    def test_latest_sort_pins_equal_time_and_dedupe(self):
        items=[{'aweme_id':'1','create_time':10,'is_top':1},{'aweme_id':'2','create_time':101},{'aweme_id':'3','create_time':100},{'aweme_id':'2','create_time':101}]
        self.assertEqual([x['aweme_id'] for x in select_recent(items,100,{'3'})],['2'])
    def test_source_id_from_user_formatted_link(self):
        self.assertEqual(video_id('[视频](https://www.douyin.com/video/7688617760657509651)'), '7688617760657509651')
        self.assertIsNone(video_id('https://example.com/'))
    def test_attachment_collision_is_not_silently_appended(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'稿.txt';p.write_text('口播')
            with self.assertRaisesRegex(RuntimeError,'CONFLICT'):missing_files([{'name':'稿.txt','size':1}], [p])
            self.assertEqual(missing_files([{'name':'稿.txt','size':p.stat().st_size}],[p]),[])
    def test_failed_upload_resumes_without_duplicate_or_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);run=root/'work/run';out=root/'out';run.mkdir(parents=True);out.mkdir()
            cfg={'workspace':str(root),'work_dir':str(root/'work'),'output_dir':str(out),'base_token':'fake','table_id':'fake','base_url':'https://example.invalid','retain_video':False}
            save(root/'config.json',cfg);w=FakeWorkflow(root/'config.json')
            media=run/'temporary/dy/media/123/video.mp4';media.parent.mkdir(parents=True);media.write_bytes(b'fake-video')
            unrelated=root/'keep.mp4';unrelated.write_bytes(b'keep')
            transcripts=out/'123';transcripts.mkdir()
            save(transcripts/'transcription.json',{'segments':[{'text':'测试完整口播'}]})
            for name in ['口播转写_机器稿.txt','口播转写_机器稿.srt']:(transcripts/name).write_text('测试完整口播')
            raw={'create_time':100,'desc':'标题 #原始标签','statistics':{'digg_count':3},'video':{'duration':1000},'text_extra':[{'hashtag_name':'原始标签'}]}
            report={'items':[{'id':'123','raw':raw,'creator':{'name':'作者','sec_user_id':'sid'},'media':str(media),'error':None}],'errors':[],'creators':{'sid':{'latest':100}}}
            save(run/'collection.json',report);save(run/'batch.json',{'run_dir':str(run),'collection':str(run/'collection.json'),'items':[{'id':'123','transcript_dir':str(transcripts)}]});save(run/'topics.json',{'123':{'选题':'提炼选题'}})
            w.fail_upload=True
            with self.assertRaisesRegex(RuntimeError,'upload failure'):w.commit(run/'batch.json',run/'topics.json')
            self.assertTrue(media.exists());self.assertNotIn('123',w.state['completed']);self.assertNotIn('sid',w.state['creators'])
            self.assertEqual(w.cloud['123']['原标题'],'标题');self.assertEqual(w.cloud['123']['Tag'],'#原始标签')
            w.cloud['123']['选题']='用户手动改过的选题';w.fail_upload=False
            w.commit(run/'batch.json',run/'topics.json')
            self.assertEqual(w.calls.count('+record-batch-create'),1);self.assertEqual(w.cloud['123']['选题'],'用户手动改过的选题')
            self.assertFalse(media.exists());self.assertTrue(unrelated.exists());self.assertEqual(len(w.cloud['123']['口播文件']),2)
            self.assertEqual(w.state['creators']['sid']['since'],100)
            w.commit(run/'batch.json',run/'topics.json')
            self.assertEqual(w.calls.count('+record-batch-create'),1);self.assertEqual(len(w.cloud['123']['口播文件']),2)
    def test_deleted_required_column_stops_without_recreation(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);save(root/'c.json',{'workspace':t,'work_dir':t+'/work','output_dir':t+'/out','base_token':'fake','table_id':'fake'})
            w=Workflow(root/'c.json');calls=[]
            def fake_cli(cmd,*a,**kw):calls.append(cmd);return {'data':{'fields':[]}}
            w.cli=fake_cli
            with self.assertRaisesRegex(RuntimeError,'SCHEMA_CHANGED'):w.schema()
            self.assertEqual(calls,['+field-list'])
    def test_no_speech_keeps_media_and_never_uploads_fake_transcript(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);run=root/'work/run';run.mkdir(parents=True)
            save(root/'config.json',{'workspace':t,'work_dir':t+'/work','output_dir':t+'/out','base_token':'fake','table_id':'fake','base_url':'https://example.invalid'})
            target=root/'out/123';target.mkdir(parents=True)
            save(target/'transcription.json',{'status':'no_speech','segments':[],'review_required':True})
            media=run/'temporary/video.mp4';media.parent.mkdir();media.write_bytes(b'needs-review')
            save(run/'collection.json',{'items':[{'id':'123','raw':{'create_time':100},'creator':{'name':'作者','sec_user_id':'sid'},'media':str(media)}],'errors':[],'creators':{'sid':{'latest':100}}})
            save(run/'batch.json',{'run_dir':str(run),'collection':str(run/'collection.json'),'items':[{'id':'123','transcript_dir':str(target)}]})
            save(run/'topics.json',{'123':{'选题':'视觉演示'}})
            w=FakeWorkflow(root/'config.json');w.commit(run/'batch.json',run/'topics.json')
            self.assertNotIn('+record-upload-attachment',w.calls)
            self.assertEqual(w.cloud['123']['采集状态'],'未识别到口播，待人工核对')
            self.assertTrue(media.exists())
            self.assertEqual(load(run/'result.json')['transcribed_count'],0)
            self.assertEqual(load(run/'result.json')['review_required_ids'],['123'])
if __name__=='__main__':unittest.main()
