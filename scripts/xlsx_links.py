"""Add native hyperlinks where Artifact Tool's HYPERLINK calculation is unavailable.

Only worksheet hyperlinks and their relationships are changed; all other ZIP
members and worksheet content remain intact. Existing links survive append.
"""
import json,sys,zipfile,os
from pathlib import Path
import xml.etree.ElementTree as E
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG='http://schemas.openxmlformats.org/package/2006/relationships'
E.register_namespace('',NS);E.register_namespace('r',REL)
sheet_path='xl/worksheets/sheet1.xml';rels_path='xl/worksheets/_rels/sheet1.xml.rels'
def extract(p):
    if p=='-':return {}
    with zipfile.ZipFile(p) as z:
        sheet=E.fromstring(z.read(sheet_path));rels=E.fromstring(z.read(rels_path)) if rels_path in z.namelist() else E.Element('{'+PKG+'}Relationships')
    lookup={r.get('Id'):r.get('Target') for r in rels};result={}
    for h in sheet.findall('{'+NS+'}hyperlinks/{'+NS+'}hyperlink'):
        rid=h.get('{'+REL+'}id')
        if rid and lookup.get(rid):result[h.get('ref')]=lookup[rid]
    return result
def col(n):
    s=''
    while n:n,d=divmod(n-1,26);s=chr(65+d)+s
    return s
if __name__=='__main__':
    dest,original,linkfile=sys.argv[1:];links=extract(original);links.update(extract(dest))
    for x in json.loads(Path(linkfile).read_text()):links[col(x['col'])+str(x['row'])]=x['target']
    with zipfile.ZipFile(dest) as z:contents={i.filename:(i,z.read(i.filename)) for i in z.infolist()}
    root=E.fromstring(contents[sheet_path][1]);rels=E.fromstring(contents[rels_path][1]) if rels_path in contents else E.Element('{'+PKG+'}Relationships')
    for x in list(rels):
        if x.get('Type','').endswith('/hyperlink'):rels.remove(x)
    old=root.find('{'+NS+'}hyperlinks')
    if old is not None:root.remove(old)
    container=E.Element('{'+NS+'}hyperlinks');ids={x.get('Id') for x in rels};n=1
    for ref,target in sorted(links.items()):
        while 'rIdLink'+str(n) in ids:n+=1
        rid='rIdLink'+str(n);n+=1
        E.SubElement(rels,'{'+PKG+'}Relationship',Id=rid,Type=REL+'/hyperlink',Target=target,TargetMode='External')
        E.SubElement(container,'{'+NS+'}hyperlink',{'ref':ref,'{'+REL+'}id':rid})
    # OOXML worksheet order: hyperlinks follow dataValidations and precede print options.
    after={'printOptions','pageMargins','pageSetup','headerFooter','rowBreaks','colBreaks','customProperties','cellWatches','ignoredErrors','smartTags','drawing','legacyDrawing','legacyDrawingHF','picture','oleObjects','controls','webPublishItems','tableParts','extLst'}
    index=next((i for i,x in enumerate(root) if x.tag.rsplit('}',1)[-1] in after),len(root));root.insert(index,container)
    patched={sheet_path:E.tostring(root,encoding='utf-8',xml_declaration=True),rels_path:E.tostring(rels,encoding='utf-8',xml_declaration=True)}
    temporary=dest+'.links-tmp'
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
        for name,(info,data) in contents.items():z.writestr(info,patched.pop(name,data))
        for name,data in patched.items():z.writestr(name,data)
    os.replace(temporary,dest)
