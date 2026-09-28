# 抖音博主内容采集 Skill

[下载最新版完整 ZIP](https://github.com/ckkaskak-collab/douyin-feishu-collector/releases/download/v2026.09.28/douyin-feishu-collector.zip) · [版本说明](https://github.com/ckkaskak-collab/douyin-feishu-collector/releases/tag/v2026.09.28)

版本：2026-09-28。以当前实际使用版本为基础，包含完整脚本、界面元数据、测试和配置模板。

采集指定抖音博主的新作品，以本地 faster-whisper 转写完整口播，提炼简短选题，增量写入飞书普通电子表格；也保留 Excel 和 Base 两种可选模式。

## 本版行为

- 原标题仅保留发布文案正文，移除话题标签；Tag 独立保存抖音 `text_extra.hashtag_name`，不添加推断标签。
- 保留原始 collection.json。支持作品 ID 去重、失败续跑、无口播待核对、云端 TXT/SRT 链接和新作品按发布时间排序。
- 只追加新作品，不恢复用户删行，不覆盖既有选题和选用状态。历史标题清理需用户单独要求。
- 无口播识别不冒充成功；登录、验证码或权限异常时停止。

## 安装和配置

1. 解压后将 `douyin-feishu-collector` 文件夹放入 Codex 的 skills 目录，保留全部子目录。
2. 准备 Python 3、已安装且已登录的 MediaCrawler、FFmpeg、本地 faster-whisper 环境，以及预先下载的 `small` 模型（脚本使用 `local_files_only=True`）。采集适配依赖 MediaCrawler 的 `DouYinCrawler`、`build_media_items` 和 `MediaDownloader` 接口；接口不匹配时先核对安装版本。
3. 飞书模式需要已完成用户身份授权的 `lark-cli` 和对应 `lark-sheets`/`lark-base` Skills；认证问题使用 `lark-shared`。不要将 Cookie、凭据、运行状态或真实配置上传至公开仓库。
4. 复制 `config.example.json` 为工作区中的真实配置，替换全部占位路径和目标表格标识。在 creators 填写经主页确认的 `sec_user_id`，然后启用该博主。
5. 路径均使用绝对路径。work_dir、output_dir、cloud_transcripts_path 和 excel_path 应在 workspace 内。即使使用 Sheets，excel_path 仍用于定位本地口播暂存目录，无需实际创建 Excel 文件。
6. Sheets 模式先准备一个工作表，第一行按以下顺序放置表头，并设置所需样式和行高。脚本复用现有表格，不自动建表：

```text
博主 | 发布时间 | 选题 | 原标题 | Tag | 点赞数 | 收藏数 | 评论数 | 分享数 | 口播TXT | 字幕SRT | 原链接 | 采集状态 | 选用状态 | 时长秒 | 平台
```

配置 transcript_folder_token 为用户可写的飞书文件夹。base_token、table_id、base_url 在 Sheets/Excel 模式可留空。Excel 模式另需 Codex 的 `@oai/artifact-tool`、Node.js 和已有的 `内容库` 工作表及 `CreatorContent` 表；相关运行时由宿主提供，本包不包含。Base 模式需配置已有表与实时字段，详见 SKILL.md。

## 执行

在配置 workspace 中运行；将 SKILL 和 CONFIG 替换为实际位置：

```bash
python3 "$SKILL/scripts/workflow.py" --config "$CONFIG" check
python3 "$SKILL/scripts/workflow.py" --config "$CONFIG" prepare
```

然后由 Codex 读完本次完整转写，为每条生成简短中性选题 JSON，再执行 `commit`。具体恢复方式和边界见 SKILL.md。安装本包不会自动创建定时任务；有需要时由用户指定运行项目和时间。

## 验证与边界

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

发布前离线测试与 ZIP CRC/逐文件 SHA-256 校验通过。标题清理规则在原用户的 47 条飞书记录上完成回读核验，其他单元格与标题样式保持不变；不代表新环境已完成账号登录或新增作品端到端采集。当前使用 fcntl 进程锁，适用于 macOS/Linux；Windows 需 WSL，未做原生 Windows 验证。

发布包只将本机路径和特定任务说明改为可配置模板，并把 Excel Python 默认回退改为 python3；其余脚本与当前安装版本一致。不包含账号配置、Cookie、模型权重、历史采集数据和转写内容。
