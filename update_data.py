#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
プール休館日カレンダー データ自動更新スクリプト（GitHub Actions用）

各プールの公式ソースを取得・解析し、index.html の
===DATA-START=== 〜 ===DATA-END=== の間（LAST_UPDATED / DATA）だけを書き換える。

DATA は JSON 互換の 1 オブジェクトで、プール id ごとに
{ "<pool_id>": {"closed": {"YYYY-MM-DD": "ラベル"}, "partial": {...}} } を持つ。
  - closed  … 全日休館（実線チップ）
  - partial … 一部時間帯のみ休止（点線チップ）
静的なプール定義（名前・色・チップ文字・ルール等）は index.html の POOLS 配列側にあり、
このスクリプトは触らない。取得や解析に失敗したソースは既存値を保持し、推測で埋めない。

新しいプールを足すとき:
  1. index.html の POOLS に定義を追加（id・名前・色・short 等）
  2. このファイルの POOLS に {"id", "kind", "parser", "url"} を追加
  3. 必要なら新しいソース型の parser 関数を書いて DISPATCH に登録
"""
import re, sys, json, datetime, unicodedata, os

RANGE_START = "2026-06-01"
RANGE_END   = "2027-03-31"
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
JST   = datetime.timezone(datetime.timedelta(hours=9))

# ---- スクレイピング対象プール（id は index.html の POOLS と対応）----
# kind: "closed"（全日休館）/ "partial"（一部休止）… パーサ結果を書き込む先
# parser: 下の DISPATCH のキー
POOLS = [
    {"id": "tac",  "kind": "closed",  "parser": "tac",
     "url": "https://www.tef.or.jp/tac/closure.html"},
    {"id": "yoko", "kind": "closed",  "parser": "yokohama",
     "url": "https://yokohama-sport.jp/waterarena/schedule/"},
    {"id": "zen",  "kind": "partial", "parser": "zengyo_partial",
     "url": "https://www.pref.kanagawa.jp/docs/ui6/1/news/pool-closing-schedule.html"},
]

def z2h(s):
    return unicodedata.normalize("NFKC", s)

def in_range(iso):
    return RANGE_START <= iso <= RANGE_END

# ---------- 東京アクアティクス：確定表(HTML) ----------
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
        if not in_range(iso):
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
        if not in_range(iso):
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
    return {k: v for k, v in out.items() if in_range(k)}

# ---------- ソース型ディスパッチ ----------
# 各 source_* は (pool, ctx, cur) を受け取り {YYYY-MM-DD: ラベル} を返す。
#   ctx.get / ctx.gettext … HTTP ヘルパ、ctx.pdfplumber … PDF ライブラリ（無ければ None）
#   cur … 既存値（フォールバックや累積マージ用）

def source_tac(pool, ctx, cur):
    return parse_tac(ctx.gettext(pool["url"]))

def source_zengyo_partial(pool, ctx, cur):
    return parse_zengyo_partial(ctx.gettext(pool["url"]))

def source_yokohama(pool, ctx, cur):
    """スケジュールページ→当月/翌月チラシPDF。既存値に上書きマージし、過去日を掃除。"""
    import io
    out = dict(cur)
    sched = ctx.gettext(pool["url"])
    pdfs = re.findall(r'href="([^"]+\.pdf)"', sched)
    pdfs = [p if p.startswith("http") else ("https://yokohama-sport.jp" + p) for p in pdfs]
    seen = set()
    for url in pdfs[:4]:
        if url in seen or ctx.pdfplumber is None:
            continue
        seen.add(url)
        data = ctx.get(url).content
        with ctx.pdfplumber.open(io.BytesIO(data)) as pdf:
            txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        out.update(parse_yokohama_text(txt))
    # 過去日を掃除（今日より前は落とす。ただし既存値にあったものは残す）
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    out = {k: v for k, v in out.items() if k >= today or k in cur}
    return out

DISPATCH = {
    "tac": source_tac,
    "zengyo_partial": source_zengyo_partial,
    "yokohama": source_yokohama,
}

# ---------- index.html 書き換え ----------
def build_block(data, updated):
    body = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    return (
        "// ===DATA-START（GitHub Actionsが自動生成。手で編集しない）===\n"
        f'const LAST_UPDATED = "{updated}";\n'
        "// プール id ごとの休館データ（closed=全日休館 / partial=一部休止）。定義は POOLS 側。\n"
        f"const DATA = {body};\n"
        "// ===DATA-END==="
    )

def extract_existing(html):
    """既存の DATA オブジェクトを読み出す（フォールバック用）。壊れていれば空。"""
    m = re.search(r"const DATA\s*=\s*(\{.*?\})\s*;", html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception as e:
        print("DATA parse failed:", e)
        return {}

class Ctx:
    def __init__(self):
        import requests
        try:
            import pdfplumber
        except Exception:
            pdfplumber = None
        self.requests = requests
        self.pdfplumber = pdfplumber
        self._ua = {"User-Agent": "Mozilla/5.0 (pool-calendar updater)"}

    def get(self, url, **kw):
        return self.requests.get(url, headers=self._ua, timeout=30, **kw)

    def gettext(self, url):
        r = self.get(url)
        r.encoding = "utf-8"   # charset未指定ページの文字化け対策
        return r.text

def main():
    ctx = Ctx()

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    existing = extract_existing(html)

    data = {}
    for pool in POOLS:
        pid, kind = pool["id"], pool["kind"]
        cur = existing.get(pid, {}).get(kind, {})
        try:
            parsed = DISPATCH[pool["parser"]](pool, ctx, cur)
            if not parsed:
                raise ValueError("empty")
        except Exception as e:
            print(f"{pid}/{kind} failed, keep existing:", e)
            parsed = cur
        # 既存の他 kind（あれば）も温存しつつ、今回の kind を差し替え
        entry = dict(existing.get(pid, {}))
        entry[kind] = parsed
        data[pid] = entry

    updated = datetime.datetime.now(JST).strftime("%Y/%-m/%-d")
    new_block = build_block(data, updated)
    new_html = re.sub(r"// ===DATA-START.*?// ===DATA-END===", new_block, html, flags=re.S)
    if new_html != html:
        with open(INDEX, "w", encoding="utf-8") as f:
            f.write(new_html)
        print("index.html updated")
    else:
        print("no change")

if __name__ == "__main__":
    main()
