"""Use the installed MediaCrawler client; no repository edits or global config writes."""
import argparse, asyncio, json, os, sys
from pathlib import Path


def select_recent(items, since, complete):
    unique = {str(i['aweme_id']): i for i in items}
    return [i for i in sorted(unique.values(), key=lambda x: x['create_time'], reverse=True)
            if str(i['aweme_id']) not in complete and (since is None or i['create_time'] >= since)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--job', required=True)
    a = p.parse_args()
    job = json.loads(Path(a.job).read_text())
    cfg = job['config']; root = Path(cfg['mediacrawler_root'])
    run = Path(job['run_dir']); run.mkdir(parents=True, exist_ok=True)
    os.chdir(root); sys.path.insert(0, str(root))
    import config
    config.PLATFORM = 'dy'; config.CRAWLER_TYPE = 'creator'
    config.ENABLE_GET_COMMENTS = False; config.ENABLE_GET_MEDIA = False
    config.SAVE_DATA_OPTION = 'json'; config.SAVE_DATA_PATH = str(run)
    config.ENABLE_CDP_MODE = False  # Use MediaCrawler's own saved profile.
    config.HEADLESS = True
    from media_platform.douyin.core import DouYinCrawler
    from media_platform.douyin.login import DouYinLogin
    from media_platform.douyin.media import build_media_items
    from media_downloader import MediaDownloader, MediaType

    async def blocked_login(self):
        raise RuntimeError('DOUYIN_LOGIN_REQUIRED: saved login expired; user must sign in or verify manually')
    DouYinLogin.begin = blocked_login
    report = {'items': [], 'creators': {}, 'errors': []}
    def save():
        target = run/'collection.json'
        temp = target.with_suffix('.tmp'); temp.write_text(json.dumps(report, ensure_ascii=False, indent=2)); temp.replace(target)

    async def collect(self):
        for creator in cfg['creators']:
            if not creator.get('enabled', True): continue
            sid = creator['sec_user_id']; since = job['state']['creators'].get(sid, {}).get('since')
            complete = set(job['state']['completed']) | set(job['cloud_complete_ids'])
            cursor = ''; all_items = {}; exhausted = False
            try:
                for page in range(cfg['max_pages_per_creator']):
                    response = await self.dy_client.get_user_aweme_posts(sid, cursor)
                    items = response.get('aweme_list')
                    if not isinstance(items, list) or (page == 0 and not items):
                        raise RuntimeError('EMPTY_OR_INVALID_RESPONSE: cannot confirm creator updates')
                    for item in items: all_items[str(item['aweme_id'])] = item
                    has_more = response.get('has_more', False)
                    regular = [i for i in items if not i.get('is_top')]
                    crossed = since is not None and regular and min(i['create_time'] for i in regular) < since
                    if not has_more or crossed:
                        exhausted = True; break
                    if since is None and len(regular) >= creator.get('initial_count', 10):
                        exhausted = True; break
                    next_cursor = str(response.get('max_cursor') or '')
                    if not next_cursor or next_cursor == cursor: raise RuntimeError('PAGINATION_STALLED')
                    cursor = next_cursor; await asyncio.sleep(2)
                if not exhausted: raise RuntimeError('PAGE_LIMIT: backlog requires an explicit catch-up run')
                selected = select_recent(list(all_items.values()), since, complete)
                if since is None: selected = selected[:creator.get('initial_count', 10)]
                if len(selected) + len(report['items']) > cfg['max_new_per_run']:
                    raise RuntimeError('NEW_ITEM_LIMIT: backlog requires an explicit catch-up run')
                downloader = MediaDownloader(platform='dy', base_dir=run/'temporary',
                    extra_headers=self._media_headers(), max_retries=1, max_candidates=2)
                for item in selected:
                    vid = str(item['aweme_id'])
                    if not vid.isdigit(): raise RuntimeError('INVALID_VIDEO_ID')
                    record = {'id': vid, 'creator': creator, 'raw': item, 'media': None, 'error': None}
                    media = [m for m in build_media_items(item) if m.media_type == MediaType.VIDEO]
                    if not media:
                        record['error'] = 'NOT_A_VIDEO_OR_NO_MEDIA: review manually'
                    elif not job.get('metadata_only', False):
                        path = await downloader.download(media[0])
                        if path and path.exists() and path.stat().st_size:
                            record['media'] = str(path)
                        else: record['error'] = 'MEDIA_DOWNLOAD_FAILED'
                    report['items'].append(record); save()
                    await asyncio.sleep(2)
                report['creators'][sid] = {'latest': max((i['create_time'] for i in all_items.values()), default=since), 'new_count': len(selected)}
            except Exception as exc:
                report['errors'].append({'creator': creator['name'], 'error': str(exc).split('\n')[0][:300]})
            save()
    DouYinCrawler.get_creators_and_videos = collect
    crawler = DouYinCrawler()
    try:
        asyncio.run(crawler.start())
    except Exception as exc:
        report['errors'].append({'stage':'login_or_start','error':str(exc).split('\n')[0][:300]}); save()
    print(json.dumps({'new_count':len(report['items']),'errors':report['errors']}, ensure_ascii=False))
    return 1 if report['errors'] or any(i.get('error') for i in report['items']) else 0

if __name__ == '__main__': raise SystemExit(main())
