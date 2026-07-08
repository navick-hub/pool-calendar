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
    {"id": "tac",  "kind": "both",    "parser": "tac",
     "url": "https://www.tef.or.jp/tac/closure.html"},
    {"id": "yoko", "kind": "both",    "parser": "yokohama",
     "url": "https://yokohama-sport.jp/waterarena/schedule/"},
    {"id": "zen",  "kind": "partial", "parser": "zengyo_partial",
     "url": "https://www.pref.kanagawa.jp/docs/ui6/1/news/pool-closing-schedule.html"},
    {"id": "chiba", "kind": "both",   "parser": "chiba",
     "url": "https://www.chiba-swim.gr.jp/"},
    # 目黒：区公式「今後の予定表」HTML表から目黒区民センター行を取得（closed=全日/partial=時間限定）
    {"id": "meguro", "kind": "both", "parser": "meguro",
     "url": "https://www.city.meguro.tokyo.jp/sports/bunkasports/sports/indoorpool_nittei.html"},
]

# スクレイプ対象外だが index.html の DATA に手動反映しているプール（上書き/削除しない）。
#   musashino … 年間PDFがカレンダー画像で自動解析不可。毎月15日ルールは index.html 側、
#               臨時休場は DATA に令和8年度PDFから手動反映（年度更新時に要見直し）。
#   sagamihara … 予定表が画像JPGのため自動解析不可。改修中(50m・飛込休止)で25mのみ。大会等は
#                画像から手動/週1スケジュールで DATA に反映（休館ルールは index.html 側）。
STATIC_POOL_IDS = {"musashino", "sagamihara"}

def z2h(s):
    return unicodedata.normalize("NFKC", s)

def in_range(iso):
    return RANGE_START <= iso <= RANGE_END

def host_allowed(url, allowed):
    """取得先URLのホストが許可リスト(公式ドメイン)に含まれるか。https限定。
    取得元ページが改ざんされリンクをすり替えられても外部URLを取りに行かないためのSSRF対策。"""
    import urllib.parse
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    if u.scheme != "https":
        return False
    host = (u.hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowed)

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
        # 多層防御：外部サイト由来の自由文字列。HTML特殊文字・制御文字を除去（表示はindex.html側でもescするが二重で防ぐ）
        label = re.sub(r'[<>"\'\r\n\t]', "", label)
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

# ---------- 横浜国際：月次チラシPDFを座標解析（メイン/サブ別）----------
YOKO_EVENT = re.compile(r"大会|選手権|競技|設営|講習|記録会|予選|カップ|大学|チャレンジ")

def parse_yokohama_pdf(pdf):
    """チラシPDF(1ページ)を座標で解析。メイン列(x<330)/サブ列(x<545)を日ごとに分離し、
    {(year,month), {day: (main_status, sub_status)}} を返す。status: 休館/大会/個人利用。"""
    pg = pdf.pages[0]
    words = [{"t": z2h(w["text"]), "x": w["x0"], "y": w["top"]} for w in pg.extract_words()]
    ym = re.search(r"(20\d\d)年\s*(\d{1,2})月予定表", " ".join(w["t"] for w in words))
    if not ym:
        return None, {}
    base = (int(ym.group(1)), int(ym.group(2)))
    dnums = sorted([w for w in words
                    if re.fullmatch(r"\d{1,2}", w["t"]) and 1 <= int(w["t"]) <= 31
                    and w["x"] < 130 and w["y"] > 110],
                   key=lambda w: w["y"])
    res = {}
    for i, w in enumerate(dnums):
        yt = w["y"] - 5
        yb = (dnums[i + 1]["y"] - 3) if i + 1 < len(dnums) else w["y"] + 25
        cell = {"main": [], "sub": []}
        for x in words:
            if yt <= x["y"] < yb and not re.fullmatch(r"\d{1,2}", x["t"]) and x["t"] not in "月火水木金土日":
                if x["x"] < 330:
                    cell["main"].append(x["t"])
                elif x["x"] < 545:
                    cell["sub"].append(x["t"])

        def status(toks):
            s = "".join(toks)
            if "休館日" in s:
                return "休館"
            if YOKO_EVENT.search(s):
                return "大会"
            return "個人利用"
        res[int(w["t"])] = (status(cell["main"]), status(cell["sub"]))
    return base, res

# ---------- ソース型ディスパッチ ----------
# 各 source_* は (pool, ctx, cur) を受け取り {YYYY-MM-DD: ラベル} を返す。
#   ctx.get / ctx.gettext … HTTP ヘルパ、ctx.pdfplumber … PDF ライブラリ（無ければ None）
#   cur … 既存値（フォールバックや累積マージ用）

# 東京アクアの水面。partialでは「使える方（利用可）」を前に出して分かりやすくする。
TAC_SURFACES = ["メイン", "サブ", "ダイビング"]

# 一般開放予定表PDF（当月・翌月分）: 大会・団体貸切で個人利用できない水面が分かる
TAC_OPEN_URL = "https://www.tef.or.jp/tac/opening.html"
TAC_SLOT = re.compile(r"^[A-F]?\d{1,2}:\d{2}$")   # コマ列の時刻表記（NFKC後）

def parse_tac_open_pdf(pdf):
    """一般開放予定表PDFを座標解析。ヘッダ語の座標は2ページ目で崩れるため、
    左右2箇所にある「コマ」列（A9:00等の時刻表記）の間をプール3列（メイン/サブ/ダイビング）として3等分する。
    返り値: ((year, month), {day: "休館" or {水面: 一般開放コマが1つでもあるか}})。情報の無い日は含めない。"""
    head = z2h(pdf.pages[0].extract_text() or "")
    m = re.search(r"令和\s*(\d+)\s*年\s*(\d{1,2})\s*月", head)
    if m:
        base = (2018 + int(m.group(1)), int(m.group(2)))
    else:
        m2 = re.search(r"(20\d\d)\s*年\s*(\d{1,2})\s*月", head)
        if not m2:
            return None, {}
        base = (int(m2.group(1)), int(m2.group(2)))
    days = {}
    for pg in pdf.pages:
        words = [{"t": z2h(w["text"]), "x0": w["x0"], "x1": w["x1"], "y": w["top"]}
                 for w in pg.extract_words()]
        slots = [w for w in words if TAC_SLOT.fullmatch(w["t"].replace(" ", ""))]
        if not slots:
            continue
        mid = (min(w["x0"] for w in slots) + max(w["x1"] for w in slots)) / 2
        lx = max(w["x1"] for w in slots if w["x0"] < mid)    # 左コマ列の右端
        rx = min(w["x0"] for w in slots if w["x0"] >= mid)   # 右コマ列の左端
        if rx - lx < 100:
            continue
        b1 = lx + (rx - lx) / 3
        b2 = lx + (rx - lx) * 2 / 3
        dnums = sorted([w for w in words
                        if re.fullmatch(r"\d{1,2}", w["t"]) and 1 <= int(w["t"]) <= 31
                        and w["x1"] < lx - 20],
                       key=lambda w: w["y"])
        uniq = []
        for w in dnums:                                      # 同一行の重複除去
            if uniq and abs(uniq[-1]["y"] - w["y"]) < 8:
                continue
            uniq.append(w)
        for i, w in enumerate(uniq):
            yt = w["y"] - 4
            yb = uniq[i + 1]["y"] - 4 if i + 1 < len(uniq) else pg.height
            day = int(w["t"])
            cell = {s: [] for s in TAC_SURFACES}
            kyukan = False
            for t in words:
                if not (yt <= t["y"] < yb):
                    continue
                if "休館日" in t["t"]:
                    kyukan = True
                cx = (t["x0"] + t["x1"]) / 2
                if not (lx < cx < rx):
                    continue
                col = "メイン" if cx < b1 else ("サブ" if cx < b2 else "ダイビング")
                cell[col].append(t["t"])
            if kyukan:
                days[day] = "休館"
            elif sum(len(v) for v in cell.values()) > 0:     # 情報なし日は推定しない
                days[day] = {k: any("一般開放" in tk for tk in v) for k, v in cell.items()}
    return base, days

def fetch_tac_open(ctx):
    """opening.html にリンクされた一般開放予定表PDFを取得し {(year,month): {day: ...}} を返す。失敗は空。"""
    import io
    out = {}
    if ctx.pdfplumber is None:
        return out
    try:
        html = ctx.gettext(TAC_OPEN_URL)
    except Exception as e:
        print("tac opening fetch failed:", e)
        return out
    seen = set()
    for p in re.findall(r'href="([^"]+\.pdf)"', html):
        if p.startswith("http"):
            url = p
        elif p.startswith("/"):
            url = "https://www.tef.or.jp" + p
        else:
            url = "https://www.tef.or.jp/tac/" + p
        if url in seen or len(seen) >= 6 or "checklist" in url:
            continue
        if not host_allowed(url, {"tef.or.jp"}):             # 公式ドメイン・https限定
            continue
        seen.add(url)
        try:
            data = ctx.get_pdf(url)
            with ctx.pdfplumber.open(io.BytesIO(data)) as pdf:
                base, days = parse_tac_open_pdf(pdf)
        except Exception as e:
            print("tac open pdf failed:", e)
            continue
        if base and days:
            out.setdefault(base, {}).update(days)
    return out

def source_tac(pool, ctx, cur):
    """closure.html（休館）と一般開放予定表PDF（大会・団体貸切）を統合。
    全館休館・全水面ふさがり=closed（実線）／一部の水面のみ=partial（点線・「利用可: ◯◯」表記）。"""
    raw = parse_tac(ctx.gettext(pool["url"]))
    closed, partial, kyukan_map = {}, {}, {}
    for iso, label in raw.items():
        if "全館" in label:
            closed[iso] = label
            continue
        ks = [s for s in TAC_SURFACES if s in label]             # 休館の水面
        usable = [s for s in TAC_SURFACES if s not in ks]        # 使える水面
        kyukan_map[iso] = set(ks)
        if ks and usable:
            partial[iso] = "利用可: " + "・".join(usable) + "（" + "・".join(ks) + "休館）"
        else:
            partial[iso] = label                                 # 想定外表記はそのまま
    # 一般開放予定表PDF: 一般開放コマが1つも無い水面＝終日 大会・貸切（PDF掲載月のみ上書き）
    for (yy, mm), days in fetch_tac_open(ctx).items():
        for day, v in days.items():
            iso = f"{yy:04d}-{mm:02d}-{day:02d}"
            if not in_range(iso):
                continue
            if v == "休館":
                closed.setdefault(iso, "全館休館")
                continue
            if iso in closed:
                continue
            ks = kyukan_map.get(iso, set())
            usable = [s for s in TAC_SURFACES if v.get(s) and s not in ks]
            blocked = [s for s in TAC_SURFACES if s not in usable]
            if not blocked:
                continue
            if not usable:                                       # 全水面ふさがり＝実質休場
                closed[iso] = "全水面 利用不可（大会・貸切等）"
                partial.pop(iso, None)
                continue
            kyu = [s for s in blocked if s in ks]                # 休館でふさがる水面
            occ = [s for s in blocked if s not in ks]            # 大会・貸切でふさがる水面
            det = []
            if kyu:
                det.append("・".join(kyu) + "休館")
            if occ:
                det.append("・".join(occ) + "=大会・貸切")
            partial[iso] = "利用可: " + "・".join(usable) + "（" + "、".join(det) + "）"
    return {"closed": closed, "partial": partial}

def source_zengyo_partial(pool, ctx, cur):
    return parse_zengyo_partial(ctx.gettext(pool["url"]))

def source_yokohama(pool, ctx, cur):
    """スケジュールページ→当月/翌月チラシPDFを座標解析。メイン/サブ別に closed/partial 生成。
    両方休館=全館休館(実線)／両方ふさがる=実質休場(実線)／片方だけ=点線「利用可: ◯◯」。"""
    import io
    if ctx.pdfplumber is None:
        raise ValueError("pdfplumber なし")
    sched = ctx.gettext(pool["url"])
    pdfs = re.findall(r'href="([^"]+\.pdf)"', sched)
    pdfs = [p if p.startswith("http") else ("https://yokohama-sport.jp" + p) for p in pdfs]
    pdfs = [p for p in pdfs if host_allowed(p, {"yokohama-sport.jp"})]   # 公式ドメイン限定
    closed, partial, seen, got = {}, {}, set(), False
    for url in pdfs[:4]:
        if url in seen:
            continue
        seen.add(url)
        try:
            data = ctx.get_pdf(url)
            with ctx.pdfplumber.open(io.BytesIO(data)) as pdf:
                base, res = parse_yokohama_pdf(pdf)
        except Exception as e:
            print("yoko pdf failed:", e)
            continue
        if not base or not res:
            continue
        got = True
        yy, mm = base
        for day, (ms, ss) in res.items():
            iso = f"{yy:04d}-{mm:02d}-{day:02d}"
            if not in_range(iso):
                continue
            usable = [n for n, s in (("メイン", ms), ("サブ", ss)) if s == "個人利用"]
            occ = [(n, s) for n, s in (("メイン", ms), ("サブ", ss)) if s != "個人利用"]
            if not usable:
                closed[iso] = "全館休館" if (ms == "休館" and ss == "休館") else "全水面 利用不可（大会等）"
            elif occ:
                det = "・".join(f"{n}={s}" for n, s in occ)
                partial[iso] = "利用可: " + "・".join(usable) + "（" + det + "）"
    if not got:
        raise ValueError("有効なチラシPDFなし")
    return {"closed": closed, "partial": partial}

# ---------- 千葉県国際：年度休場日PDF（テキスト抽出可）----------
def parse_chiba_text(pdf_text):
    """年度休場日PDFのテキストから {YYYY-MM-DD:"休場"} を返す。
    形式例: "6月 8日(月)" / "11月 4日(水)～11月20日(金)"。年度は "２０２６年度" から。"""
    t = z2h(pdf_text)
    ym = re.search(r"(20\d\d)\s*年度", t)
    base = int(ym.group(1)) if ym else datetime.datetime.now(JST).year
    def yof(m):
        return base if m >= 4 else base + 1   # 年度：4月以降は base、1〜3月は翌年
    out = {}
    day = r"(\d{1,2})月\s*(\d{1,2})日\([日月火水木金土]\)"
    # 期間: M月D日(曜)～M月D日(曜)。～はNFKCで ~(U+007E) になる/〜(U+301C)のまま等ゆれるので全部許容
    rng = re.compile(day + r"\s*[~〜～]\s*" + day)
    for m1, d1, m2, d2 in rng.findall(t):
        m1, d1, m2, d2 = int(m1), int(d1), int(m2), int(d2)
        cur = datetime.date(yof(m1), m1, d1)
        end = datetime.date(yof(m2), m2, d2)
        while cur <= end:
            iso = cur.strftime("%Y-%m-%d")
            if in_range(iso):
                out[iso] = "休場"
            cur += datetime.timedelta(days=1)
    # 単日（期間表記を除いてから拾う）
    t2 = rng.sub("", t)
    for m, d in re.findall(day, t2):
        m, d = int(m), int(d)
        iso = datetime.date(yof(m), m, d).strftime("%Y-%m-%d")
        if in_range(iso):
            out[iso] = "休場"
    return out

def _chiba_closed_from_pdf(pool, ctx):
    """トップページから「休場日」を含むPDFリンクを探し、全館休場日 {iso:"休場"} を返す。"""
    import io, urllib.parse
    top = ctx.gettext(pool["url"])
    links = re.findall(r'href="([^"]+\.pdf)"', top)
    pdf = None
    for l in links:
        if "休場日" in urllib.parse.unquote(l):
            cand = l if l.startswith("http") else ("https://www.chiba-swim.gr.jp" + l)
            # 公式ドメインのPDFのみ取得（改ざんによる外部URL誘導を防ぐ）
            if host_allowed(cand, {"chiba-swim.gr.jp"}):
                pdf = cand
                break
    if not pdf or ctx.pdfplumber is None:
        raise ValueError("休場日PDFリンクが見つからない")
    data = ctx.get_pdf(pdf)
    with ctx.pdfplumber.open(io.BytesIO(data)) as p:
        txt = "\n".join((pg.extract_text() or "") for pg in p.pages)
    return parse_chiba_text(txt)

# 水面別カレンダー（公開HTML）。メイン/サブ/飛込 が別 facility_id で日別状態を持つ。
# セル値: ""=一般開放の空き / "閉場"=時間外(その水面は非営業) / 大会・全面使用・大会準備=イベント占有 / 休場日
CHIBA_SURFACES = {1: "メイン", 2: "サブ", 3: "飛込"}
CHIBA_OCC = ("大会", "全面使用", "大会準備")   # イベント占有（一般利用不可の要因）

def parse_chiba_calendar(html_text):
    """reserve/calendar の表から {日(int): [各時間帯セルの文字列]} を返す（""＝一般開放の空き）。"""
    import html as H
    m = re.search(r'<table class="calendar"[\s\S]*?</table>', html_text)
    if not m:
        return {}
    out = {}
    for row in re.findall(r"<tr[\s\S]*?</tr>", m.group(0)):
        cells = [H.unescape(re.sub(r"<[^>]+>", "", c)).strip().replace("\xa0", "")
                 for c in re.findall(r"<t[dh][\s\S]*?</t[dh]>", row)]
        if not cells:
            continue
        md = re.match(r"^(\d+)/", cells[0])   # 例 "13/月"
        if not md:
            continue
        out[int(md.group(1))] = cells[1:]
    return out

def source_chiba(pool, ctx, cur):
    """水面別に集計。closed=年度休場PDF＋『全水面が大会等で使えない＝実質休場』（実線）。
    partial=一部の水面だけ終日ふさがるが他は使える日（点線・利用可の水面を明記）。"""
    closed = _chiba_closed_from_pdf(pool, ctx)
    partial = {}
    now = datetime.datetime.now(JST)
    months, y, mo = [], now.year, now.month
    for _ in range(3):                        # 当月＋2か月（大会予定が入る近未来のみ）
        months.append((y, mo))
        mo += 1
        if mo > 12:
            mo = 1; y += 1
    for (yy, mm) in months:
        cals = {}
        for fid in CHIBA_SURFACES:
            url = f"https://www.chiba-swim.gr.jp/reserve/calendar?facility_id={fid}&year={yy}&month={mm}"
            if not host_allowed(url, {"chiba-swim.gr.jp"}):
                continue
            try:
                cals[fid] = parse_chiba_calendar(ctx.gettext(url))
            except Exception as e:
                print(f"chiba calendar {fid}/{yy}-{mm} failed:", e)
        if not cals:
            continue
        days = set().union(*[set(c.keys()) for c in cals.values()])
        for day in days:
            iso = f"{yy:04d}-{mm:02d}-{day:02d}"
            if not in_range(iso) or iso in closed:      # 公式休場は PDF 側に集約済み
                continue
            usable, blocked = [], []                    # blocked=イベントで終日ふさがった水面
            any_event = any_kyujo = False
            for fid, name in CHIBA_SURFACES.items():
                cells = cals.get(fid, {}).get(day, [])
                has_open = any(c == "" for c in cells)
                has_event = any(c in CHIBA_OCC for c in cells)
                any_event = any_event or has_event
                any_kyujo = any_kyujo or ("休場日" in cells)
                if has_open:
                    usable.append(name)
                elif has_event:                         # 空き無し＋イベント＝その水面は終日不可
                    blocked.append(name)
            if usable:
                if blocked:                             # 一部の水面だけ不可（他は使える）＝点線
                    partial[iso] = "一部制限（利用可: " + "・".join(usable) + "）"
            elif any_event:                             # どの水面も使えない＝実質休場（実線）
                closed[iso] = "全水面 利用不可（大会・占有等）"
            elif any_kyujo:                             # 全水面 休場日
                closed[iso] = "休場"
    return {"closed": closed, "partial": partial}

# ---------- 目黒区民センター：区公式「今後の予定表」HTML表 ----------
def parse_meguro_cell(cell):
    """目黒区民センター行のセル文言を {closed:{iso:label}, partial:{iso:label}} に。
    例: "6月8日(月)・9日(火)9:00から12:00 6月10日(水)・12日(金)9:00から13:00 7月6日(月)から9日(木)"
    時間付き=部分休館(partial)、時間なし=全日休場(closed)、「から」で日付範囲は展開。推測で埋めない。"""
    t = z2h(cell)
    now = datetime.datetime.now(JST)
    fy = now.year if now.month >= 4 else now.year - 1   # 年度開始年
    def yof(m): return fy if m >= 4 else fy + 1
    closed, partial = {}, {}
    # エントリは "M月D日(" の直前で分割（後続の "D日(" は月が無いので割れない）
    for e in re.split(r"(?=\d{1,2}月\d{1,2}日\()", t):
        e = e.strip()
        if not re.match(r"\d{1,2}月\d{1,2}日", e):
            continue
        month = int(re.match(r"(\d{1,2})月", e).group(1))
        tm = re.search(r"(\d{1,2}):(\d{2})から(\d{1,2}):(\d{2})", e)   # 時間帯（=部分休館）
        label_time = None
        core = e
        if tm:
            label_time = f"{int(tm.group(1))}:{tm.group(2)}-{int(tm.group(3))}:{tm.group(4)} 利用不可"
            core = e[:tm.start()]
        rng = re.search(r"(\d{1,2})月(\d{1,2})日\([^)]*\)から(?:(\d{1,2})月)?(\d{1,2})日", core)
        dts = []
        if rng:
            m1, d1 = int(rng.group(1)), int(rng.group(2))
            m2, d2 = int(rng.group(3) or rng.group(1)), int(rng.group(4))
            cur = datetime.date(yof(m1), m1, d1)
            end = datetime.date(yof(m2), m2, d2)
            while cur <= end:
                dts.append(cur); cur += datetime.timedelta(days=1)
        else:
            first = re.match(r"(\d{1,2})月(\d{1,2})日", core)
            if first:
                dts.append(datetime.date(yof(int(first.group(1))), int(first.group(1)), int(first.group(2))))
            for dm in re.findall(r"・(\d{1,2})日", core):   # ・で並ぶ2つ目以降は月を継承
                dts.append(datetime.date(yof(month), month, int(dm)))
        for dt in dts:
            iso = dt.strftime("%Y-%m-%d")
            if not in_range(iso):
                continue
            if label_time:
                partial[iso] = label_time
            else:
                closed[iso] = "臨時休場"
    return {"closed": closed, "partial": partial}

def source_meguro(pool, ctx, cur):
    html = ctx.gettext(pool["url"])
    tbl = re.search(r"<table[\s\S]*?</table>", html)
    if not tbl:
        raise ValueError("予定表テーブルが見つからない")
    for tr in re.findall(r"<tr[\s\S]*?</tr>", tbl.group(0)):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip()
                 for c in re.findall(r"<t[dh][\s\S]*?</t[dh]>", tr)]
        cells = [c for c in cells if c]
        if cells and "目黒区民センター" in cells[0] and len(cells) >= 2:
            return parse_meguro_cell(cells[1])
    raise ValueError("目黒区民センター行が見つからない")

DISPATCH = {
    "tac": source_tac,
    "zengyo_partial": source_zengyo_partial,
    "yokohama": source_yokohama,
    "chiba": source_chiba,
    "meguro": source_meguro,
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

    def get_pdf(self, url, limit=10 * 1024 * 1024):
        """PDF取得専用。リダイレクト不追従（host_allowed検証済みURLから他ホストへ飛ばされない）＋
        サイズ上限つきストリーム読み（巨大ファイルでの更新ジョブのメモリ枯渇防止）。"""
        r = self.get(url, stream=True, allow_redirects=False)
        r.raise_for_status()
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > limit:
                raise ValueError(f"PDF too large (> {limit} bytes): {url}")
        return buf

    def gettext(self, url):
        r = self.get(url)
        r.encoding = "utf-8"   # charset未指定ページの文字化け対策
        return r.text

def main():
    ctx = Ctx()

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    existing = extract_existing(html)

    # 既存DATAを土台に（STATIC_POOL_IDS など未管理プールの手動反映分を温存）
    data = dict(existing)
    for pool in POOLS:
        pid, kind = pool["id"], pool["kind"]
        if kind == "both":
            cur = existing.get(pid, {})
            try:
                parsed = DISPATCH[pool["parser"]](pool, ctx, cur)
                if not (parsed.get("closed") or parsed.get("partial")):
                    raise ValueError("empty")
            except Exception as e:
                print(f"{pid}/both failed, keep existing:", e)
                parsed = {"closed": cur.get("closed", {}), "partial": cur.get("partial", {})}
            entry = dict(existing.get(pid, {}))
            entry["closed"] = parsed.get("closed", {})
            entry["partial"] = parsed.get("partial", {})
            data[pid] = entry
            continue
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
