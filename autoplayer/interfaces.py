"""Small, evidence-only interface rules. Ambiguous OCR falls back to observation."""
import re
import unicodedata

# Match confirmation sentences, including punctuation/line breaks between the
# operation and the question. A menu entry named 中断 alone is not a question.
PROTECTED_CONFIRMATION=re.compile(
    r'削除|初期化|購入|課金|終了しますか|'
    r'(?:(?:終了|中断)(?:します|する|しても|して)|やめ(?:ます|る|ても))'
    r'[\s\S]{0,24}(?:よろし|いいです|良いです|か[?？])|'
    r'delete|purchase|reset\s+save',re.I)



def compact_text(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text))


def has_text_content(native):
    """Letters/ideographs provide text context; lone numbers/symbols do not."""
    return bool(native and native.get('status')=='ok' and any(char.isalpha() for line in native.get('lines',[]) for char in compact_text(line.get('text',''))))


def ordinary_choice(native):
    """Require independent option labels; はい in prose is not a choice."""
    if not native or native.get('status','ok')!='ok':return False
    labels={compact_text(line.get('text','')).lstrip('▶►▷>→') for line in native.get('lines',[])}
    return {'はい','いいえ'}<=labels or {'yes','no'}<={label.casefold() for label in labels}


ITEM_ACTION_LABELS={'食べる','飲む','読む','振る','ふる','うつ','撃つ','装備','外す','投げる','なげる','置く','説明','使う','名前','名付ける'}

MAIN_MENU_LABELS={'道具','どうぐ','マップ','足元','その他','プレイ記録','倉庫','せってい','設定','中断'}

def normalize_item_menu(native):
    """Join only adjacent OCR fragments forming a known action label.

    Preserve original fragments as evidence. No correction by edit distance or
    by assuming an expected command is present.
    """
    if not native or native.get('status')!='ok':return native
    lines=list(native.get('lines',[]));changed=False
    def bounds(line):
        boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        return (min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[0]+b[2] for b in boxes),max(b[1]+b[3] for b in boxes)) if boxes else None
    for _ in range(3):
        pair=None
        for i,left in enumerate(lines):
            text=compact_text(left.get('text',''))
            if not text or len(text)>3 or not any(command.startswith(text) and command!=text for command in ITEM_ACTION_LABELS):continue
            a=bounds(left)
            if not a:continue
            for j,right in enumerate(lines):
                if i==j:continue
                joined=text+compact_text(right.get('text',''))
                if joined not in ITEM_ACTION_LABELS:continue
                b=bounds(right)
                if not b:continue
                h=max(a[3]-a[1],b[3]-b[1]);gap=b[0]-a[2]
                if h>0 and b[0]>a[0] and -h*.2<=gap<=h*.8 and abs((a[1]+a[3]-b[1]-b[3])/2)<=h*.4:
                    pair=(i,j,joined);break
            if pair:break
        if not pair:break
        i,j,joined=pair;left,right=lines[i],lines[j]
        merged={**left,'text':joined,'words':left.get('words',[])+right.get('words',[]),'ocr_fragments':[left.get('text',''),right.get('text','')],'text_basis':'adjacent_same_row_ocr_fragments'}
        lines[i]=merged;lines.pop(j);changed=True
    if not changed:return native
    result={**native,'lines':lines};result.pop('_visual_selection',None)
    return result


def item_popup_layout(native):
    """Two aligned action entries plus an inventory counter corroborate a popup."""
    native=normalize_item_menu(native) or {};lines=native.get('lines',[])
    if not any(re.fullmatch(r'\d{1,2}/20',compact_text(l.get('text',''))) for l in lines):return False
    labels={compact_text(l.get('text','')).lstrip('▶►▷>→') for l in lines}
    if len(labels & ITEM_ACTION_LABELS)<2:return False
    entries=[]
    for line in lines:
        if compact_text(line.get('text','')).lstrip('▶►▷>→') not in ITEM_ACTION_LABELS:continue
        boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        if boxes:entries.append((min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[3] for b in boxes)))
    return any(abs(a[0]-b[0])<=max(a[2],b[2])*.6 and 0<abs(a[1]-b[1])<=max(a[2],b[2])*3 for i,a in enumerate(entries) for b in entries[i+1:])


def main_menu_context(native):
    labels={compact_text(l.get('text','')).lstrip('▶►▷>→') for l in (native or {}).get('lines',[])}
    return {'labels':sorted(labels&MAIN_MENU_LABELS),'extra_text':sorted(t for t in labels-MAIN_MENU_LABELS if sum(c.isalpha() for c in t)>=8)}


def torneko_menu_kind(native):
    """Require multiple independent visible labels, never an LLM menu caption."""
    if not native or native.get('status')!='ok':return None
    normalized=normalize_item_menu(native)
    labels={compact_text(line.get('text','')).lstrip('▶►▷>→') for line in normalized.get('lines',[])}
    text='\n'.join(labels)
    if re.search(r'削除|初期化|購入|課金|終了|はい|いいえ',text):return None
    if '持ち出しアイテム' in labels and len(labels & {'詳細表示切替','整理','反映'})>=2:return 'carry_inventory'
    if len(labels & ITEM_ACTION_LABELS)>=3 and '投げる' in labels and '置く' in labels or item_popup_layout(normalized):
        return 'item_actions'
    if ('道具' in labels and '詳細表示切替' in text and '整理' in text
            and any(re.fullmatch(r'\d{1,3}/\d{1,3}',label) for label in labels)):
        return 'inventory'
    if len(labels & {'道具','どうぐ','マップ','足元','その他'})>=3:return 'main_menu'
    if '道具' in labels and len(labels & {'プレイ記録','倉庫','せってい','設定','中断','その他'})>=2:return 'main_menu'
    return None


def result_screen(native):
    if not native or native.get('status')!='ok':return None
    labels=[compact_text(l.get('text','')) for l in native.get('lines',[])]
    text='\n'.join(labels)
    if re.search(r'削除|初期化|購入|課金|終了しますか|はい|いいえ',text):return None
    if ('今回の冒険の結果' in labels and '最深階' in text and 'スコア' in text
            and re.search(r'倒され|死亡|力尽き',text)):
        return 'defeat_result'
    if 'ハイスコア' in labels:
        ranks=sum(bool(re.fullmatch(r'\d{1,2}:',s)) for s in labels)
        # OCR may merge the rank column into 12345678 and several rows into a
        # single line. Repeated score AND floor entries still identify the table.
        scores=re.findall(r'(?<![\d,])\d[\d,]*G',text,re.I)
        floors=re.findall(r'\d{1,3}階',text)
        active_hud=bool(re.search(r'HP.{0,8}\d+[/／]\d+|満腹度',text,re.I))
        if not active_hud and scores and (ranks>=3 or len(scores)>=3 and len(floors)>=3):
            return 'high_scores'
    return None



def choice_action(native, bindings, allowed):
    if native.get('status') != 'ok':
        return None
    lines = native.get('lines', [])
    text = unicodedata.normalize('NFKC', '\n'.join(l.get('text','') for l in lines))
    if re.search(r'削除|初期化|購入|課金|終了|delete|purchase|quit|reset\s+save', text, re.I):
        return None
    confirm = next((k for k,v in bindings.items() if re.search(r'決定|confirm',v,re.I) and k in allowed), None)
    if not confirm:
        return None
    options = []
    for line in lines:
        label = re.sub(r'\s+', '', unicodedata.normalize('NFKC',line.get('text','')))
        # Exact option labels only; quoted words in dialogue are not options.
        match = re.fullmatch(r'([▶►▷>→]?)\s*(はい|いいえ|Yes|No)',label,re.I)
        if not match:
            continue
        boxes = [w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        position = (min(b[0] for b in boxes),min(b[1] for b in boxes)) if boxes else None
        options.append({'yes':match[2].casefold() in ('はい','yes'),'selected':bool(match[1]),'position':position})
    selected = [o for o in options if o['selected']]
    if len(selected)!=1:
        return None
    if selected[0]['yes']:
        return {'key':confirm,'reason':'OCRの独立した選択肢行でカーソル付きの「はい」を確認。決定を1回送信。'}
    yes = [o for o in options if o['yes']]
    current = selected[0]['position']
    if len(yes)!=1 or not yes[0]['position'] or not current:
        return None
    dx,dy = (yes[0]['position'][i]-current[i] for i in (0,1))
    if not dx and not dy:
        return None
    direction = ('右' if dx>0 else '左') if abs(dx)>abs(dy) else ('下' if dy>0 else '上')
    key = next((k for k,v in bindings.items() if (v.startswith(direction) or v.lower() in {'上':'up','下':'down','左':'left','右':'right'}[direction]) and k in allowed),None)
    return {'key':key,'reason':'選択肢行の配置から「はい」へ'+direction+'入力。決定は次の画面で選択を確認してから行う。'} if key else None
