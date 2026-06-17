#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
プール休館日カレンダー データ自動更新スクリプト（GitHub Actions用）
3つの公式ソースを取得・解析し、index.html の ===DATA-START=== 〜 ===DATA-END===
の間（TAC / YOKO / ZEN_PART / LAST_UPDATED）を書き換える。
取得や解析に失敗したソースは既存値を保持し、推測で埋めない。
"""
import re, sys, json, datetime, unicodedata, os

RANGE_START = "2026-06-01"
RANGE_END   = "2027-03-31"
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

def z2h(s):
    return unicodedata.normalize("NFKC", s)

# ---------- 東京アクアティクス ----------
def parse_tac(html):
    """HTML/テキストから プール影響の休館日 を {YYYY-MM-DD: ラベル} で返す"""
    text = re.sub(r"<[^>]+>", " ", html)          # タグ除去
    text = z2h(text)
    out = {}
    # 例: 2026 11 7（土） 休館日（メインプール、ダイビングプール）
    # z2h(NFKC)後は全角カッコが半角になる点に注意
    pat = re.compile(r"(20\d\d)[\s|]{0,4}(\d{1,2})[\s|]{0,4}(\d{1,2})\([日月火水木金土]\)[\s|]{0,6}休館日\(([^)]+)\)")
    for y, m, d, body in pat.findall(text):
        # プールに影響する日だけ（トレーニングルームのみは除外）
        if not re.search(r"全館|メインプール|サブプール|ダイビングプール", body):
            continue
        iso = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        if iso < RANGE_START or iso > RANGE_END:
            continue
        if "全館" in body:
            label = "全館休館"
        else:
            b = body.replace("トレーニングルーム", "").replace("プール", "")
            b = re.sub(r"[、，]+", "・", b).strip("・　 ")
            label = (b + "休館") if b else "休館"
        out[iso] = label
    return out

# ---------- 善行：一部利用休止 ----------
def parse_zengyo_partial(html):
    text = re.sub(r"<[^>]+>", "", html)
    text = z2h(text)
    text = re.sub(r"\s+", "", text)   # 空白を全除去（日付と時刻の間にタグ/空白が入る対策）
    out = {}
    # 例: 令和8年6月17日（水曜日）16時から21時まで
    pat = re.compile(r"令和(\d+)年(\d{1,2})月(\d{1,2})日\([^)]*\)(\d{1,2})時から(\d{1,2})時まで")
    for r_, m, d, h1, h2 in pat.findall(text):
        y = 2018 + int(r_)
        iso = f"{y:04d}-{int(m):02d}-{int(d):02d}"
        if iso < RANGE_START or iso > RANGE_END:
            continue
        out[iso] = f"{int(h1)}:00-{int(h2)}:00 利用休止"
    return out

# ---------- 横浜国際：月次チラシPDFのテキストから ----------
def parse_yokohama_text(pdf_text):
    """1枚のチラシPDFのテキストから確定休館日 {YYYY-MM-DD:"休館日"} を返す"""
    t = z2h(pdf_text)
    out = {}
    # チラシの年月（例: 2026年6月予定表）
    ym = re.search(r"(20\d\d)年\s*(\d{1,2})月予定表", t)
    base_year = int(ym.group(1)) if ym else datetime.date.today().year
    base_month = int(ym.group(2)) if ym else datetime.date.today().month
    def to_iso(m, d):
        y = base_year + (1 if m < base_month else 0)
        return f"{y:04d}-{int(m):02d}-{int(d):02d}"
    # 「次回休館日：9月24日（木）」
    for m, d in re.findall(r"次回休館日[：: ]*(\d{1,2})月(\d{1,2})日", t):
        out[to_iso(int(m), int(d))] = "休館日"
    # 「休館日のご案内 … 6月16日（火）」: 休館日の近くにある M月D日（曜）
    for mobj in re.finditer(r"休館日", t):
        seg = t[mobj.start(): mobj.start()+40]
        for m, d in re.findall(r"(\d{1,2})月(\d{1,2})日\([日月火水木金土]\)", seg):
            out[to_iso(int(m), int(d))] = "休館日"
    # 範囲内のみ
    return {k: v for k, v in out.items() if RANGE_START <= k <= RANGE_END}

# ---------- index.html 書き換え ----------
def js_dict(d):
    items = ",\n ".join(f'"{k}":"{v}"' for k, v in sorted(d.items()))
    return "{\n " + items + "\n}" if d else "{}"

def build_block(tac, yoko, zen_part, updated):
    return (
        "// ===DATA-START（GitHub Actionsが自動生成。手で編集しない）===\n"
        f'const LAST_UPDATED = "{updated}";\n'
        "// 確定：東京アクアティクス（プール影響日のみ／公式表）\n"
        f"const TAC = {js_dict(tac)};\n"
        "// 確定：横浜国際（公式チラシ）。10月以降は未発表\n"
        f"const YOKO = {js_dict(yoko)};\n"
        "// 確定：善行 一部利用休止（公式「利用休止」ページ）\n"
        f"const ZEN_PART = {js_dict(zen_part)};\n"
        "// ===DATA-END==="
    )

def extract_existing(html):
    """既存の TAC/YOKO/ZEN_PART を読み出す（フォールバック用）"""
    def grab(name):
        m = re.search(name + r"\s*=\s*(\{.*?\})\s*;", html, re.S)
        if not m:
            return {}
        body = m.group(1)
        return dict(re.findall(r'"([0-9\-]+)"\s*:\s*"([^"]*)"', body))
    return grab("TAC"), grab("YOKO"), grab("ZEN_PART")

def main():
    import requests
    try:
        import pdfplumber
    except Exception:
        pdfplumber = None
    UA = {"User-Agent": "Mozilla/5.0 (pool-calendar updater)"}
    def get(url, **kw):
        return requests.get(url, headers=UA, timeout=30, **kw)
    def gettext(url):
        r = get(url); r.encoding = "utf-8"; return r.text  # charset未指定ページの文字化け対策

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    cur_tac, cur_yoko, cur_zen = extract_existing(html)

    # TAC
    try:
        tac = parse_tac(gettext("https://www.tef.or.jp/tac/closure.html"))
        if not tac: raise ValueError("empty")
    except Exception as e:
        print("TAC failed, keep existing:", e); tac = cur_tac

    # 善行 一部休止
    try:
        zp = parse_zengyo_partial(gettext("https://www.pref.kanagawa.jp/docs/ui6/1/news/pool-closing-schedule.html"))
        if not zp: raise ValueError("empty")
    except Exception as e:
        print("ZEN_PART failed, keep existing:", e); zp = cur_zen

    # 横浜国際（スケジュールページ→当月/翌月PDF）
    yoko = dict(cur_yoko)
    try:
        sched = gettext("https://yokohama-sport.jp/waterarena/schedule/")
        pdfs = re.findall(r'href="([^"]+\.pdf)"', sched)
        pdfs = [p if p.startswith("http") else ("https://yokohama-sport.jp"+p) for p in pdfs]
        seen = set()
        for url in pdfs[:4]:
            if url in seen or pdfplumber is None: continue
            seen.add(url)
            import io
            data = get(url).content
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
            yoko.update(parse_yokohama_text(txt))
    except Exception as e:
        print("YOKO failed, keep existing:", e)
    # 過去日を掃除（今日より前は落とす）
    today = (datetime.datetime.utcnow()+datetime.timedelta(hours=9)).strftime("%Y-%m-%d")
    yoko = {k:v for k,v in yoko.items() if k >= today or k in cur_yoko}

    updated = (datetime.datetime.utcnow()+datetime.timedelta(hours=9)).strftime("%Y/%-m/%-d")
    new_block = build_block(tac, yoko, zp, updated)
    new_html = re.sub(r"// ===DATA-START.*?// ===DATA-END===", new_block, html, flags=re.S)
    if new_html != html:
        with open(INDEX, "w", encoding="utf-8") as f:
            f.write(new_html)
        print("index.html updated")
    else:
        print("no change")

if __name__ == "__main__":
    main()
