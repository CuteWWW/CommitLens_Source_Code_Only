import re, zlib, sys, codecs

path = r"C:\Users\Lenovo\Desktop\project_5351\week1-cs5351-project (1).pdf"
data = open(path, 'rb').read()

# ---------- object table ----------
objs = {}   # num -> (dict_bytes, stream_bytes or None)

def find_objects(buf):
    res = {}
    for m in re.finditer(rb'(?<![0-9])(\d+)\s+(\d+)\s+obj\b', buf):
        start = m.end()
        # dictionary region: until 'stream' or 'endobj'
        rest = buf[start:start+4000000]
        ms = re.search(rb'\bstream\r?\n', rest)
        me = re.search(rb'\bendobj\b', rest)
        if ms and (not me or ms.start() < me.start()):
            d = rest[:ms.start()]
            length = None
            lm = re.search(rb'/Length\s+(\d+)(\s+\d+\s+R)?', d)
            if lm and not lm.group(2):
                length = int(lm.group(1))
            if length is not None:
                s = rest[ms.end():ms.end()+length]
            else:
                # fallback: to endstream
                es = rest.find(b'endstream', ms.end())
                s = rest[ms.end():es]
            res[int(m.group(1))] = (d, s, int(m.group(2)))
        else:
            d = rest[:me.start()] if me else rest[:2000]
            res[int(m.group(1))] = (d, None, int(m.group(2)))
    return res

objs = find_objects(data)

def get_stream(num):
    if num not in objs: return None
    d, s, _ = objs[num]
    if s is None: return None
    if b'/FlateDecode' in d:
        try: return zlib.decompress(s)
        except Exception:
            try: return zlib.decompressobj().decompress(s)
            except Exception: return s
    return s

def deref(tok):
    m = re.match(rb'\s*(\d+)\s+\d+\s+R', tok)
    if m: return int(m.group(1))
    return None

# ---------- expand object streams ----------
for num in list(objs):
    d, s, _ = objs[num]
    if b'/ObjStm' in d:
        content = get_stream(num)
        if not content: continue
        nm = re.search(rb'/N\s+(\d+)', d); fm = re.search(rb'/First\s+(\d+)', d)
        if not (nm and fm): continue
        n = int(nm.group(1)); first = int(fm.group(1))
        header = content[:first].split()
        try:
            pairs = [(int(header[i]), int(header[i+1])) for i in range(0, 2*n, 2)]
        except Exception:
            continue
        for i, (onum, off) in enumerate(pairs):
            end = pairs[i+1][1] + first if i+1 < len(pairs) else len(content)
            body = content[first+off:end]
            objs.setdefault(onum, (body, None, 0))

# ---------- page tree ----------
def resolve_dict(num):
    return objs.get(num, (b'', None, 0))[0]

root_num = None
for num, (d, s, g) in objs.items():
    if b'/Type' in d and b'/Catalog' in d:
        root_num = num; break

def kids_of(num):
    d = resolve_dict(num)
    m = re.search(rb'/Kids\s*\[(.*?)\]', d, re.S)
    if not m: return []
    return [int(x) for x in re.findall(rb'(\d+)\s+\d+\s+R', m.group(1))]

pages = []
def walk(num, depth=0):
    d = resolve_dict(num)
    if b'/Type' in d and re.search(rb'/Type\s*/Page\b', d):
        pages.append(num); return
    for k in kids_of(num):
        walk(k, depth+1)

if root_num is not None:
    cd = resolve_dict(root_num)
    pm = re.search(rb'/Pages\s+(\d+)\s+\d+\s+R', cd)
    if pm: walk(int(pm.group(1)))

if not pages:
    pages = [n for n, (d, s, g) in objs.items() if re.search(rb'/Type\s*/Page\b', d)]

# ---------- font decoding ----------
def parse_cmap(stream):
    mapping = {}
    txt = stream.decode('latin-1', 'replace')
    for blk in re.findall(r'beginbfchar(.*?)endbfchar', txt, re.S):
        for a, b in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            try:
                src = int(a, 16)
                dst = ''.join(chr(int(b[i:i+4], 16)) for i in range(0, len(b), 4))
                mapping[src] = dst
            except Exception: pass
    for blk in re.findall(r'beginbfrange(.*?)endbfrange', txt, re.S):
        for a, b, c in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            try:
                lo, hi, dst = int(a, 16), int(b, 16), int(c, 16)
                for i in range(lo, min(hi, lo+65535)+1):
                    mapping[i] = chr(dst + (i-lo))
            except Exception: pass
    return mapping

GLYPHS = {'space':' ','period':'.','comma':',','colon':':','semicolon':';','hyphen':'-','endash':'\u2013','emdash':'\u2014','parenleft':'(','parenright':')','slash':'/','quotesingle':"'",'quotedbl':'"','ampersand':'&','percent':'%','numbersign':'#','at':'@','bullet':'\u2022','equal':'=','plus':'+','asterisk':'*','question':'?','exclam':'!','zero':'0','one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9'}

def font_info(fontnum):
    d = resolve_dict(fontnum)
    info = {'cmap': None, 'two_byte': False, 'encoding': {}}
    if b'/Type0' in d or b'Identity-H' in d:
        info['two_byte'] = True
    tm = re.search(rb'/ToUnicode\s+(\d+)\s+\d+\s+R', d)
    if tm:
        st = get_stream(int(tm.group(1)))
        if st: info['cmap'] = parse_cmap(st)
    em = re.search(rb'/Encoding\s*<<(.*?)>>', d, re.S)
    if em:
        for a, g in re.findall(rb'(\d+)\s*/([A-Za-z0-9_.]+)', em.group(1)):
            info['encoding'][int(a)] = GLYPHS.get(g.decode(), '')
    return info

def decode_str(raw, fi):
    if fi['cmap']:
        out = []
        if fi['two_byte']:
            for i in range(0, len(raw)-1, 2):
                code = (raw[i] << 8) | raw[i+1]
                out.append(fi['cmap'].get(code, ''))
        else:
            for b in raw:
                out.append(fi['cmap'].get(b, chr(b)))
        return ''.join(out)
    if fi['two_byte']:
        out = []
        for i in range(0, len(raw)-1, 2):
            code = (raw[i] << 8) | raw[i+1]
            out.append(chr(code) if 32 <= code < 0xFFFD else '')
        return ''.join(out)
    return ''.join(fi['encoding'].get(b, chr(b)) for b in raw)

# ---------- content stream text ----------
TOKEN = re.compile(rb"""
    (?P<str>\((?:\\.|[^\\()])*\))
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<arr>\[)
  | (?P<arrend>\])
  | (?P<name>/[^\s/\[\]()<>{}]+)
  | (?P<num>-?\d*\.?\d+)
  | (?P<op>[A-Za-z'"*]+)
  | (?P<other>\S)
""", re.X | re.S)

def unescape(s):
    s = s[1:-1]
    out = bytearray(); i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5c and i+1 < len(s):
            n = s[i+1]
            mp = {ord('n'):10, ord('r'):13, ord('t'):9, ord('b'):8, ord('f'):12,
                  ord('('):40, ord(')'):41, ord('\\'):92}
            if n in mp:
                out.append(mp[n]); i += 2; continue
            m = re.match(rb'[0-7]{1,3}', s[i+1:i+4])
            if m:
                out.append(int(m.group(), 8) & 0xFF); i += 1 + len(m.group()); continue
            i += 1; continue
        out.append(c); i += 1
    return bytes(out)

def hexstr(h):
    h = re.sub(rb'[^0-9A-Fa-f]', b'', h[1:-1])
    if len(h) % 2: h += b'0'
    return bytes.fromhex(h.decode())

def extract_content(content, fonts):
    if not content: return ''
    lines = []
    cur = []
    font = None
    last_y = None
    arr_depth = 0
    arr_tokens = []
    for m in TOKEN.finditer(content):
        kind = m.lastgroup
        tok = m.group()
        if kind == 'op':
            op = tok.decode('latin-1')
            if op == 'Tf':
                if arr_tokens:
                    for t in reversed(arr_tokens):
                        if t[0] == 'name':
                            fn = t[1]
                            font = fonts.get(fn)
                            break
            elif op in ('Tj', "'", '"'):
                if arr_tokens:
                    t = arr_tokens[-1]
                    if t[0] == 'str':
                        if op in ("'", '"') and cur:
                            lines.append(''.join(cur)); cur = []
                        cur.append(decode_str(t[1], font or {'cmap':None,'two_byte':False,'encoding':{}}))
            elif op == 'TJ':
                # arr_tokens: [ '[' , items..., ']' ]
                for t in arr_tokens:
                    if t[0] == 'str':
                        cur.append(decode_str(t[1], font or {'cmap':None,'two_byte':False,'encoding':{}}))
                    elif t[0] == 'num':
                        try:
                            if float(t[1]) < -250 and cur and not cur[-1].endswith(' '):
                                cur.append(' ')
                        except Exception: pass
            elif op in ('Td', 'TD', 'Tm', 'T*'):
                ys = [t for t in arr_tokens if t[0] == 'num']
                if op in ('Td', 'TD') and len(ys) >= 2:
                    ty = float(ys[-1][1]); tx = float(ys[-2][1])
                    if abs(ty) > 0.5 or abs(tx) > 40:
                        if cur:
                            lines.append(''.join(cur)); cur = []
                elif op == 'Tm' and len(ys) >= 6:
                    y = float(ys[-1][1])
                    if last_y is not None and abs(y - last_y) > 1:
                        if cur:
                            lines.append(''.join(cur)); cur = []
                    last_y = y
                elif op == 'T*':
                    if cur:
                        lines.append(''.join(cur)); cur = []
            elif op == 'ET':
                if cur:
                    lines.append(''.join(cur)); cur = []
            arr_tokens = []
            arr_depth = 0
        elif kind == 'arr':
            arr_tokens = []; arr_depth += 1
        elif kind == 'arrend':
            arr_depth = max(0, arr_depth-1)
        elif kind == 'str':
            arr_tokens.append(('str', unescape(tok)))
        elif kind == 'hex':
            arr_tokens.append(('str', hexstr(tok)))
        elif kind == 'name':
            arr_tokens.append(('name', tok[1:].decode('latin-1')))
        elif kind == 'num':
            arr_tokens.append(('num', tok.decode()))
    if cur: lines.append(''.join(cur))
    return '\n'.join(lines)

out_pages = []
for idx, pnum in enumerate(pages, 1):
    d = resolve_dict(pnum)
    # resources
    res = None
    rm = re.search(rb'/Resources\s+(\d+)\s+\d+\s+R', d)
    if rm: res = resolve_dict(int(rm.group(1)))
    else:
        rm = re.search(rb'/Resources\s*<<(.*)', d, re.S)
        if rm: res = rm.group(1)
    fonts = {}
    if res:
        fm = re.search(rb'/Font\s*<<(.*?)>>', res, re.S)
        if fm:
            for name, num in re.findall(rb'/([^\s/]+)\s+(\d+)\s+\d+\s+R', fm.group(1)):
                fonts[name.decode()] = font_info(int(num))
        else:
            fm = re.search(rb'/Font\s+(\d+)\s+\d+\s+R', res)
            if fm:
                fd = resolve_dict(int(fm.group(1)))
                for name, num in re.findall(rb'/([^\s/]+)\s+(\d+)\s+\d+\s+R', fd):
                    fonts[name.decode()] = font_info(int(num))
    # contents
    contents = []
    cm = re.search(rb'/Contents\s+(\d+)\s+\d+\s+R', d)
    if cm:
        contents = [int(cm.group(1))]
    else:
        cm = re.search(rb'/Contents\s*\[(.*?)\]', d, re.S)
        if cm:
            contents = [int(x) for x in re.findall(rb'(\d+)\s+\d+\s+R', cm.group(1))]
    text = '\n'.join(extract_content(get_stream(c), fonts) for c in contents)
    out_pages.append((idx, text))

with open(r'C:\Users\Lenovo\Desktop\project_5351\pdf_text.txt', 'w', encoding='utf-8') as f:
    for idx, text in out_pages:
        f.write(f'\n===== PAGE {idx} =====\n')
        f.write(text.strip() + '\n')
print('pages', len(out_pages))
print(sum(len(t) for _, t in out_pages))
