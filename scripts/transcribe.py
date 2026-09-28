"""Full local Chinese ASR; does not claim proofreading or silently truncate audio."""
import argparse, json, subprocess
from pathlib import Path


def stamp(t):
    n=round(t*1000)
    return f'{n//3600000:02}:{n//60000%60:02}:{n//1000%60:02},{n%1000:03}'


def main():
    p=argparse.ArgumentParser(); p.add_argument('--config',required=True); p.add_argument('--media',required=True); p.add_argument('--out',required=True)
    a=p.parse_args();cfg=json.loads(Path(a.config).read_text());out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    done=out/'transcription.json'
    if done.exists() and json.loads(done.read_text()).get('status')=='no_speech':
        print('NO_SPEECH_RECORDED: requires review'); return
    if done.exists() and all((out/n).is_file() for n in ['口播转写_机器稿.txt','口播转写_机器稿.srt']):
        print('TRANSCRIPT_EXISTS'); return
    audio=out/'temporary-audio.wav'
    subprocess.run([cfg['ffmpeg'],'-v','error','-y','-i',a.media,'-vn','-ac','1','-ar','16000',str(audio)],check=True,timeout=600)
    from faster_whisper import WhisperModel
    model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=6,local_files_only=True)
    segments,info=model.transcribe(str(audio),language='zh',beam_size=5,vad_filter=True)
    data=[]
    for s in segments:
        data.append({'start':round(s.start,3),'end':round(s.end,3),'text':s.text.strip()})
        if len(data)%30==0: print(f'ASR_PROGRESS {s.end:.0f}/{info.duration:.0f}s',flush=True)
    if not data or not ''.join(s['text'] for s in data).strip():
        done.write_text(json.dumps({'source':'faster-whisper small','status':'no_speech','proofread':False,'duration':info.duration,'segments':[], 'review_required':True},ensure_ascii=False,indent=2))
        audio.unlink()
        print('NO_SPEECH: metadata only; requires review'); return
    (out/'口播转写_机器稿.txt').write_text('\n'.join(s['text'] for s in data))
    (out/'口播转写_机器稿.srt').write_text('\n\n'.join(f'{i}\n{stamp(s["start"])} --> {stamp(s["end"])}\n{s["text"]}' for i,s in enumerate(data,1)))
    done.write_text(json.dumps({'source':'faster-whisper small','proofread':False,'duration':info.duration,'segments':data},ensure_ascii=False,indent=2))
    audio.unlink()  # Only this script's own temporary audio.
    print(json.dumps({'duration':info.duration,'segments':len(data),'proofread':False}))

if __name__=='__main__':main()
