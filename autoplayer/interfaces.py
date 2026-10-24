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


def torneko_menu_kind(native):
    """Require multiple independent visible labels, never an LLM menu caption."""
    if not native or native.get('status')!='ok':return None
    labels={compact_text(line.get('text','')) for line in native.get('lines',[])}
    text='\n'.join(labels)
    if re.search(r'削除|初期化|購入|課金|終了|はい|いいえ',text):return None
    if len(labels & {'食べる','飲む','読む','振る','装備','外す','投げる','置く','説明'})>=3 and '投げる' in labels and '置く' in labels:
        return 'item_actions'
    if ('道具' in labels and '詳細表示切替' in text and '整理' in text
            and any(re.fullmatch(r'\d{1,3}/\d{1,3}',label) for label in labels)):
        return 'inventory'
    if len(labels & {'道具','どうぐ','マップ','足元','その他'})>=3:return 'main_menu'
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
