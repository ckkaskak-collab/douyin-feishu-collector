import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {FileBlob, SpreadsheetFile, Workbook} from '@oai/artifact-tool';

const [mode, jobPath] = process.argv.slice(2);
const job = JSON.parse(await fs.readFile(jobPath, 'utf8'));
const headers = ['博主','发布时间','选题','原标题','Tag','点赞数','收藏数','评论数','分享数','口播TXT','字幕SRT','原链接','采集状态','选用状态','时长秒','平台'];
const widths=[20,23,48,64,42,12,12,12,12,14,14,53,32,15,12,10];
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const quote = s => String(s).replaceAll('"','""');
const literal = v => typeof v === 'string' && v.startsWith('=') ? "'"+v : v;
const exists = p => fs.access(p).then(()=>true,()=>false);
const original = await exists(job.xlsx) ? await fs.readFile(job.xlsx) : null;
if (mode === 'create' && original) throw Error('OUTPUT_EXISTS');
if (mode !== 'create' && !original) throw Error('WORKBOOK_MISSING');
const wb = original ? await SpreadsheetFile.importXlsx(await FileBlob.load(job.xlsx)) : Workbook.create();
const sheet = original ? wb.worksheets.getItem('内容库') : wb.worksheets.add('内容库');
let table = original ? sheet.tables.items.find(t=>t.name==='CreatorContent') : null;
if (original && !table) throw Error('TABLE_MISSING: CreatorContent');
let names = original ? table.getHeaderRowRange().values[0] : headers;
const records = () => table.getDataRows().map(row=>Object.fromEntries(names.map((n,i)=>[n,row[i]])));
if (mode === 'read') {
  await fs.writeFile(job.report,JSON.stringify({records:records()},null,2));
  process.exit(0);
}
for (const n of ['原链接','博主','发布时间']) if (!names.includes(n)) throw Error('REQUIRED_COLUMN_MISSING: '+n);
const before = original ? records() : [];
const seen = new Set(before.map(r=>r['原链接']));
if (seen.size !== before.length) throw Error('DUPLICATE_SOURCE_URL');
const incoming = job.records.filter(r=>!seen.has(r['原链接']));
if (new Set(incoming.map(r=>r['原链接'])).size !== incoming.length) throw Error('DUPLICATE_INCOMING_URL');
const values = incoming.map(r=>names.map(n=>n==='发布时间' ? new Date(r[n].replace(' ','T')+'Z') : literal(r[n]??null)));
const links=[];
const start = before.length+2;
if (!original) {
  sheet.getRangeByIndexes(0,0,1,names.length).values=[names];
  if(values.length) sheet.getRangeByIndexes(1,0,values.length,names.length).values=values;
  table=sheet.tables.add(`A1:P${values.length+1}`,true,'CreatorContent');
  table.style='TableStyleLight9';
  table.showFilterButton=true;
  sheet.showGridLines=false;
  sheet.tabColor='#24476A';
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(2);
  widths.forEach((w,c)=>sheet.getRangeByIndexes(0,c,values.length+1,1).format.columnWidth=w);
  sheet.getRange('A1:P1').format={fill:'#24476A',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},horizontalAlignment:'center',verticalAlignment:'center',rowHeight:30};
} else if (values.length) table.rows.add(null,values);
if (values.length) {
  const body=sheet.getRangeByIndexes(start-1,0,values.length,names.length);
  body.format.font={name:'Arial',size:11,color:'#243746'};
  body.format.verticalAlignment='center';
  body.format.wrapText=true;
  for (let c=0;c<names.length;c++) {
    const range=sheet.getRangeByIndexes(start-1,c,values.length,1);
    if (names[c]==='发布时间') range.setNumberFormat('yyyy-mm-dd hh:mm');
    if (['点赞数','收藏数','评论数','分享数'].includes(names[c])) range.setNumberFormat('#,##0');
    if (names[c]==='时长秒') range.setNumberFormat('0.00');
    if (names[c]==='选用状态') range.dataValidation={rule:{type:'list',values:['待筛选','已选用','暂不选用']}};
  }
  for (let i=0;i<incoming.length;i++) {
    const r=incoming[i];
    for (const [n,key,label] of [['口播TXT','txt','打开口播'],['字幕SRT','srt','打开字幕'],['原链接','url',r['原链接']]]) {
      const c=names.indexOf(n); if(c<0)continue;
      const target=key==='url'?r['原链接']:r[key];
      if(target) {
        const cell=sheet.getCell(start-1+i,c);
        cell.values=[[label]];
        links.push({row:start+i,col:c+1,target});
        cell.format.font={name:'Arial',size:11,color:'#1765A5',underline:'single'};
      }
    }
  }
  for (let i=0;i<incoming.length;i++) {
    const lines=Math.max(2,...names.map((name,c)=>String(incoming[i][name]??'').split('\n').reduce((sum,line)=>sum+Math.max(1,Math.ceil([...line].reduce((n,ch)=>n+(ch.charCodeAt(0)>255?1.6:0.8),0)/(widths[headers.indexOf(name)]||24))),0)));
    sheet.getRangeByIndexes(start-1+i,0,1,names.length).format.rowHeight=lines*15+10;
  }
}
wb.recalculate();
const all=records();
if (all.length!==before.length+incoming.length) throw Error('ROW_COUNT_MISMATCH');
if (original && JSON.stringify(all.slice(0,before.length))!==JSON.stringify(before)) throw Error('EXISTING_VALUES_CHANGED');
const scan=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:10},maxChars:1500});
console.log(scan.ndjson);
if(job.preview) {
  const img=await wb.render({sheetName:'内容库',range:'A1:H5',scale:1.5,format:'png'});
  await fs.writeFile(job.preview,new Uint8Array(await img.arrayBuffer()));
}
await fs.mkdir(path.dirname(job.xlsx),{recursive:true});
const temp=job.xlsx.replace(/\.xlsx$/,'.pending.xlsx');
const xlsx=await SpreadsheetFile.exportXlsx(wb);await xlsx.save(temp);
const linksPath=job.report+'.links.json';await fs.writeFile(linksPath,JSON.stringify(links));
execFileSync(job.python||'python3',[path.join(path.dirname(fileURLToPath(import.meta.url)),'xlsx_links.py'),temp,original?job.xlsx:'-',linksPath],{stdio:'pipe'});
const check=await SpreadsheetFile.importXlsx(await FileBlob.load(temp));
const saved=check.worksheets.getItem('内容库').tables.items.find(t=>t.name==='CreatorContent');
if (!saved || saved.getDataRows().length!==all.length) throw Error('SAVED_FILE_VERIFY_FAILED');
if (original) {
  if(await exists(path.join(path.dirname(job.xlsx),'~$'+path.basename(job.xlsx)))) throw Error('EXCEL_OPEN: close workbook and retry');
  if(hash(await fs.readFile(job.xlsx))!==hash(original)) throw Error('WORKBOOK_CHANGED_DURING_UPDATE');
  await fs.mkdir(job.backupDir,{recursive:true});
  await fs.writeFile(path.join(job.backupDir,`${Date.now()}-${path.basename(job.xlsx)}`),original,{flag:'wx'});
}
await fs.rename(temp,job.xlsx);
await fs.writeFile(job.report,JSON.stringify({count:all.length,added:incoming.length,records:all},null,2));
console.log(JSON.stringify({count:all.length,added:incoming.length,xlsx:job.xlsx}));
