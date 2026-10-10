"""
Læser en .xlsx med standardbiblioteket alene (zip + xml) — intet skal
installeres. Kun værdier, ingen formatering; formler læses som deres
senest beregnede værdi.

    from xlsx_stdlib import load
    for sheet_name, rows in load("fil.xlsx"):
        ...
"""
import sys, zipfile, re, xml.etree.ElementTree as ET
NS={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
def col(ref):
    n=0
    for ch in re.match(r"[A-Z]+",ref).group(0): n=n*26+ord(ch)-64
    return n-1
def load(path):
    z=zipfile.ZipFile(path)
    ss=[]
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si",NS):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t"%NS["m"])))
    wb=ET.fromstring(z.read("xl/workbook.xml"))
    rels=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid={r.get("Id"):r.get("Target") for r in rels}
    out=[]
    for sh in wb.find("m:sheets",NS):
        tgt=rid[sh.get("{%s}id"%NS["r"])]
        tgt=tgt.lstrip("/"); tgt=tgt if tgt.startswith("xl/") else "xl/"+tgt
        root=ET.fromstring(z.read(tgt)); rows=[]
        for r in root.iter("{%s}row"%NS["m"]):
            cells={}
            for c in r.findall("m:c",NS):
                v=c.find("m:v",NS); t=c.get("t")
                if t=="inlineStr":
                    val="".join(x.text or "" for x in c.iter("{%s}t"%NS["m"]))
                elif v is None: continue
                elif t=="s": val=ss[int(v.text)]
                else: val=v.text
                cells[col(c.get("r"))]=val
            if cells:
                w=max(cells)+1; rows.append([cells.get(i,"") for i in range(w)])
        out.append((sh.get("name"),rows))
    return out
if __name__=="__main__":
    for name,rows in load(sys.argv[1]):
        print(f"=== ARK: {name} — {len(rows)} rækker, op til {max((len(r) for r in rows),default=0)} kolonner")
