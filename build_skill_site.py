# -*- coding: utf-8 -*-
"""
ETF策略信号看板 生成器 (自包含版, 纯标准库, 可部署到 GitHub Actions 每日自动更新)
三类策略:
  ① 量化交易信号   : MACD/RSI/KDJ/均线/布林带 五项指标 → 多空信号统计
  ② A股短线交易决策 : 动量+低位+资金流 加权短线评分 → 可交易/观望
  ③ A股超短线交易策略 : 方法论卡片 + 对ETF的适用边界
数据: 新浪K线接口(内置 fetch_kline), 标的池=热门板块ETF前20(同花顺热搜, 概念+行业合并, 按热度排序)。
输出: 单文件离线 index.html (三tab切换, 不联网)。
"""
import os, datetime, statistics, json, urllib.request, ssl

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}
THS_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://eq.10jqka.com.cn/",
}

# 同花顺热搜抓取失败时的备用标的池(常见宽基/行业ETF)
FALLBACK_ETFS = [
    ("sh510300", "沪深300ETF"), ("sh510500", "中证500ETF"), ("sz159915", "创业板ETF"),
    ("sh518880", "黄金ETF"), ("sh512100", "中证1000ETF"), ("sz159919", "沪深300ETF"),
    ("sh510050", "上证50ETF"), ("sz159949", "创业板50ETF"), ("sh512660", "军工ETF"),
    ("sh512480", "半导体ETF"), ("sz159995", "芯片ETF"), ("sh512760", "芯片ETF"),
    ("sh515030", "新能源ETF"), ("sz159770", "机器人ETF"), ("sh588000", "科创50ETF"),
    ("sz159892", "恒生医药ETF"), ("sh513180", "恒生科技ETF"), ("sz159920", "恒生ETF"),
    ("sh512010", "医药ETF"), ("sh512690", "酒ETF"),
]


def http_get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_kline(symbol, datalen=160):
    """从新浪财经抓取日K线, 返回按时间升序的 [{day,close,volume,...}]"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (symbol, datalen))
    req = urllib.request.Request(url, headers=SINA_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        raw = resp.read().decode("gbk")
    data = json.loads(raw)
    rows = [{"day": d["day"], "open": float(d["open"]), "high": float(d["high"]),
             "low": float(d["low"]), "close": float(d["close"]), "volume": float(d["volume"])}
            for d in data]
    rows.sort(key=lambda x: x["day"])
    return rows


def fetch_ths_hot20():
    """抓取热门板块ETF前20(概念+行业合并, 按热度排序去重)"""
    base = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate"
    items = []
    for typ in ("concept", "industry"):
        try:
            j = http_get_json(base + "?type=" + typ, THS_HEADERS)
            for it in j["data"]["plate_list"]:
                code = str(it.get("etf_product_id", "")).strip()
                if not code:
                    continue
                mid = it.get("etf_market_id") or it.get("market_id")
                if str(mid) == "20":
                    pref = "sh"
                elif str(mid) == "36":
                    pref = "sz"
                else:
                    pref = "sh" if code[0] in "56" else "sz"
                items.append({
                    "code": pref + code,
                    "name": it.get("etf_name", ""),
                    "plate": it.get("name", ""),
                    "rate": float(it.get("rate", 0) or 0),
                    "tag": it.get("hot_tag", ""),
                })
        except Exception as e:
            print("  [热搜] 抓取 %s 失败: %s" % (typ, e))
    seen = {}
    for it in items:
        c = it["code"]
        if c not in seen or it["rate"] > seen[c]["rate"]:
            seen[c] = it
    uniq = list(seen.values())
    uniq.sort(key=lambda x: x["rate"], reverse=True)
    top = uniq[:20]
    if not top:
        print("  [热搜] 抓取为空, 回退到备用ETF池")
        return FALLBACK_ETFS
    print("  热门板块ETF前20(概念+行业合并, 按热度):")
    for i, t in enumerate(top, 1):
        print("   %2d %s(%s) 板块:%s 热度%.0f %s" % (i, t["name"], t["code"], t["plate"], t["rate"], t["tag"]))
    return [(t["code"], t["name"]) for t in top]


ETFS = fetch_ths_hot20()

# ===================== 技术指标函数 =====================
def ma(closes, n):
    return sum(closes[-n:]) / n

def ema_series(vals, n):
    k = 2.0 / (n + 1)
    e = [vals[0]]
    for v in vals[1:]:
        e.append(v * k + e[-1] * (1 - k))
    return e

def macd(closes, fast=12, slow=26, sig=9):
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    dif = [ef[i] - es[i] for i in range(len(closes))]
    dea = ema_series(dif, sig)
    return dif[-1], dea[-1], dif[-2], dea[-2]

def rsi(closes, n=14):
    if len(closes) < 2:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))][-n:]
    g = sum(x for x in deltas if x > 0) / n
    l = -sum(x for x in deltas if x < 0) / n
    if l == 0:
        return 100.0
    return 100 - 100 / (1 + g / l)

def kdj(highs, lows, closes, n=9):
    rsvs = []
    for i in range(len(closes)):
        if i < n - 1:
            rsvs.append(50.0); continue
        hh = max(highs[i - n + 1:i + 1]); ll = min(lows[i - n + 1:i + 1])
        rsvs.append(50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100)
    ks = [rsvs[0]]
    for v in rsvs[1:]: ks.append((2.0/3) * ks[-1] + (1.0/3) * v)
    ds = [ks[0]]
    for v in ks[1:]: ds.append((2.0/3) * ds[-1] + (1.0/3) * v)
    k, d, j = ks[-1], ds[-1], 3 * ks[-1] - 2 * ds[-1]
    gold = ks[-2] <= ds[-2] and ks[-1] > ds[-1]
    dead = ks[-2] >= ds[-2] and ks[-1] < ds[-1]
    return k, d, j, gold, dead

# ===================== ① 量化交易信号 =====================
def analyze_quant(rows):
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    n = len(closes); cur = closes[-1]
    rsi_v = rsi(closes, 14)
    dif1, dea1, dif0, dea0 = macd(closes)
    k, d, j, k_gold, k_dead = kdj(highs, lows, closes)
    ma5, ma10, ma20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
    ma60 = ma(closes, 60) if n >= 60 else ma(closes, min(60, n))
    bull_align = ma5 > ma10 > ma20
    bear_align = ma5 < ma10 < ma20
    mid = ma20; std = statistics.pstdev(closes[-20:]) or 0.0001
    up, dn = mid + 2 * std, mid - 2 * std
    bw = (up - dn) / mid * 100
    break_up, break_dn = cur > up, cur < dn

    long_s = short_s = 0
    if dif0 <= dea0 and dif1 > dea1: long_s += 1
    if dif0 >= dea0 and dif1 < dea1: short_s += 1
    if k_gold: long_s += 1
    if k_dead: short_s += 1
    if j > 100: short_s += 1
    if j < 0: long_s += 1
    if bull_align: long_s += 1
    if bear_align: short_s += 1
    if break_up: long_s += 1
    if break_dn: short_s += 1
    if rsi_v > 70: short_s += 1
    if rsi_v < 30: long_s += 1
    net = long_s - short_s

    if net >= 1: verdict, vcls = "偏多", "up"
    elif net <= -1: verdict, vcls = "偏空", "down"
    else: verdict, vcls = "中性", "mid"
    stars = "⭐⭐⭐" if abs(net) >= 2 else ("⭐⭐" if net != 0 else "")
    is_range = (bw < 6 and net == 0)

    return {
        "rsi": rsi_v, "macd": (dif1 - dea1), "k": k, "d": d, "j": j,
        "bull": bull_align, "bear": bear_align, "break_up": break_up,
        "break_dn": break_dn, "bw": bw, "net": net, "long_s": long_s,
        "short_s": short_s, "verdict": verdict, "vcls": vcls, "stars": stars,
        "is_range": is_range,
    }

# ===================== ② A股短线交易决策 =====================
def analyze_short(rows):
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    n = len(closes); cur = closes[-1]
    r10 = (cur / closes[-11] - 1) * 100 if n > 10 else 0
    r20 = (cur / closes[-21] - 1) * 100 if n > 20 else 0
    win = closes[-60:]
    lo, hi = min(win), max(win)
    v_pct = (cur - lo) / (hi - lo) * 100 if hi > lo else 50
    v_score = 100 - v_pct
    f_ratio = (sum(vols[-5:]) / 5) / (sum(vols[-20:]) / 20)
    rsi_v = rsi(closes, 14)

    m_s = max(0, min(40, r20 * 2 + 20))
    v_s = v_score * 0.3
    f_s = max(0, min(30, (f_ratio - 0.8) * 30 + 15))
    total = m_s + v_s + f_s
    pass_risk = rsi_v < 80
    tradable = pass_risk and total >= 50
    return {
        "m_s": m_s, "v_s": v_s, "f_s": f_s, "total": total,
        "rsi": rsi_v, "f_ratio": f_ratio, "v_pct": v_pct,
        "tradable": tradable, "pass": pass_risk,
    }

# ===================== HTML 渲染 =====================
def cell_up(t):   return '<span class="up">%s</span>' % t
def cell_down(t): return '<span class="down">%s</span>' % t
def cell_mid(t):  return '<span class="mid">%s</span>' % t

def render_quant(items):
    long_n = sum(1 for x in items if x["q"]["verdict"] == "偏多")
    short_n = sum(1 for x in items if x["q"]["verdict"] == "偏空")
    neutral_n = sum(1 for x in items if x["q"]["verdict"] == "中性")
    range_n = sum(1 for x in items if x["q"]["is_range"])
    rows_html = ""
    for x in items:
        q = x["q"]
        macd_cell = cell_up("金叉") if (q["long_s"] >= q["short_s"] and q["macd"] > 0) else (cell_down("死叉") if q["macd"] < 0 else cell_mid("—"))
        rsi_cell = cell_down("超买>70") if q["rsi"] > 70 else (cell_up("超卖<30") if q["rsi"] < 30 else cell_mid("%.0f" % q["rsi"]))
        kdj_cell = cell_up("金叉") if q["k"] > q["d"] else cell_down("死叉")
        ma_cell = cell_up("多头") if q["bull"] else (cell_down("空头") if q["bear"] else cell_mid("纠缠"))
        bl_cell = cell_up("破上轨") if q["break_up"] else (cell_down("破下轨") if q["break_dn"] else cell_mid("%.1f%%带宽" % q["bw"]))
        vcell = cell_up(q["verdict"] + q["stars"]) if q["vcls"] == "up" else (cell_down(q["verdict"] + q["stars"]) if q["vcls"] == "down" else cell_mid(q["verdict"] + q["stars"]))
        rows_html += ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                      "<td>%s</td><td>%s</td><td>%s</td></tr>") % (
            x["code"], x["name"], macd_cell, rsi_cell, kdj_cell, ma_cell, bl_cell, vcell)
    cards = ("<div class=\"kpi\"><div class=\"kpi-n up\">%d</div><div class=\"kpi-l\">偏多 · 基金数</div></div>"
             "<div class=\"kpi\"><div class=\"kpi-n down\">%d</div><div class=\"kpi-l\">偏空 · 基金数</div></div>"
             "<div class=\"kpi\"><div class=\"kpi-n mid\">%d</div><div class=\"kpi-l\">中性 · 基金数</div></div>"
             "<div class=\"kpi\"><div class=\"kpi-n warn\">%d</div><div class=\"kpi-l\">震荡(布林窄) · 基金数</div></div>") % (
        long_n, short_n, neutral_n, range_n)
    return cards, rows_html

def render_short(items):
    trade_n = sum(1 for x in items if x["s"]["tradable"])
    watch_n = len(items) - trade_n
    rows_html = ""
    for x in items:
        s = x["s"]
        m_cell = cell_up("%.0f" % s["m_s"]) if s["m_s"] >= 20 else (cell_down("%.0f" % s["m_s"]) if s["m_s"] < 10 else cell_mid("%.0f" % s["m_s"]))
        v_cell = cell_up("%.0f" % s["v_s"]) if s["v_s"] >= 20 else cell_mid("%.0f" % s["v_s"])
        f_cell = cell_up("%.0f" % s["f_s"]) if s["f_s"] >= 20 else cell_mid("%.0f" % s["f_s"])
        t_cell = cell_up("%.1f" % s["total"]) if s["total"] >= 50 else cell_mid("%.1f" % s["total"])
        verdict = cell_up("✅ 可交易") if s["tradable"] else cell_mid("⏸ 观望")
        rows_html += ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>") % (
            x["code"], x["name"], m_cell, v_cell, f_cell, t_cell, verdict)
    cards = ("<div class=\"kpi\"><div class=\"kpi-n up\">%d</div><div class=\"kpi-l\">可短线交易 · 基金数</div></div>"
             "<div class=\"kpi\"><div class=\"kpi-n mid\">%d</div><div class=\"kpi-l\">观望 · 基金数</div></div>"
             "<div class=\"kpi\"><div class=\"kpi-n\">%d</div><div class=\"kpi-l\">跟踪基金总数</div></div>") % (trade_n, watch_n, len(items))
    return cards, rows_html

METHOD_CARD = """
<div class="method">
  <h3>⚡ 核心定位</h3>
  <p>游牧型超短线交易者 —— <b>只做主线、只做最强、只赚爆发段</b>。原计划面向 A股个股（打板/连板），对 ETF 需做边界裁剪（见下方红字）。</p>

  <h3>🎯 选股/选标的标准</h3>
  <table class="mt">
    <tr><th>维度</th><th>原计划(个股)</th><th>对ETF的适用</th></tr>
    <tr><td>主线题材</td><td>政策/事件/资金驱动的板块</td><td class="ok">✅ 适用：选当下最强行业/宽基ETF</td></tr>
    <tr><td>流通市值</td><td>50-300亿</td><td class="na">❌ 不适用：ETF看规模与流动性</td></tr>
    <tr><td>换手率</td><td>5%-15%</td><td class="ok">✅ 参考：看成交量活跃度</td></tr>
    <tr><td>板块涨停数≥3</td><td>确认题材持续</td><td class="na">❌ 不适用：ETF无涨停板博弈</td></tr>
    <tr><td>龙头>跟风</td><td>率先涨停/成交最大</td><td class="na">❌ 不适用：ETF是篮子，无龙头个股</td></tr>
  </table>

  <h3>💰 仓位管理（原计划按50万本金）</h3>
  <table class="mt">
    <tr><th>情况</th><th>仓位</th><th>对ETF</th></tr>
    <tr><td>强主线+强龙头</td><td>50%（25万，1-2只）</td><td class="ok">✅ 可迁移：单标的≤50%</td></tr>
    <tr><td>一般机会</td><td>30%（15万，1只）</td><td class="ok">✅ 可迁移</td></tr>
    <tr><td>弱信号/试错</td><td>10-15%（5-7万）</td><td class="ok">✅ 可迁移</td></tr>
  </table>
  <p class="badge">铁律：单只不超50%仓位 · 永远保留底仓现金 · 绝不临时起意加仓</p>

  <h3>🛑 止损纪律（可完整迁移到ETF）</h3>
  <table class="mt">
    <tr><th>类型</th><th>规则</th></tr>
    <tr><td>固定止损</td><td class="ok">✅ 买入价 -5% 无条件执行</td></tr>
    <tr><td>日内止损</td><td class="ok">✅ 当日收盘亏>3%，次日竞价出</td></tr>
    <tr><td>题材止损</td><td class="ok">✅ 主线逻辑证伪，无论盈亏立刻走</td></tr>
  </table>

  <h3>📉 回撤控制（可迁移）</h3>
  <p>单月回撤≥10% → 下一笔前强制复盘；<b>连亏3次 → 强制停手1-3天</b>；账户回撤达15% → 降至半仓以下。</p>

  <h3>📌 ETF 超短线适用边界总结</h3>
  <p class="badge warn">不适用（删除）：首板打板、二板确认、龙头/跟风、创业板20%/北交所30%弹性、板块涨停数筛选 —— 这些是<b>个股涨停板博弈</b>，ETF 没有。</p>
  <p class="badge ok">可迁移（保留）：仓位纪律（单标的≤50%、留现金）、固定/日内/题材三类止损、连亏停手与回撤控制、只做主线的思路。</p>
  <p class="quote">“短线交易的核心不是选股，是纪律。计划你的交易，交易你的计划。”</p>
</div>
"""

CSS = """
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#ffffff;color:#111111;font-family:-apple-system,'Microsoft YaHei',Segoe UI,sans-serif;font-size:13px;line-height:1.6;}
.topnav{position:sticky;top:0;z-index:99;background:#ffffff;display:flex;align-items:center;gap:16px;padding:10px 16px;border-bottom:2px solid #111111;flex-wrap:wrap;}
.brand{font-weight:800;font-size:16px;color:#111111;white-space:nowrap;}
.brand small{font-weight:400;color:#666;font-size:11px;margin-left:6px;}
.datebadge{margin-left:auto;background:#fafafa;border:1px solid #e3e3e3;border-radius:8px;padding:6px 12px;font-size:12px;color:#c8102e;white-space:nowrap;}
.tabs{display:flex;gap:8px;flex-wrap:wrap;}
.tabbtn{background:#ffffff;color:#111111;border:1px solid #d8d8d8;border-radius:8px;padding:8px 13px;font-size:13px;cursor:pointer;transition:.15s;}
.tabbtn:hover{background:#f6f6f6;}
.tabbtn.active{background:#c8102e;color:#ffffff;border-color:#c8102e;}
.tabpane{padding:14px 16px 0;}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 14px;}
.kpi{flex:1;min-width:120px;background:#fafafa;border:1px solid #e3e3e3;border-radius:12px;padding:14px;text-align:center;}
.kpi-n{font-size:30px;font-weight:800;}
.kpi-l{font-size:12px;color:#666;margin-top:4px;}
.up{color:#c8102e;} .down{color:#8a8a8a;} .mid{color:#888;} .warn{color:#c8102e;}
h3{color:#111111;margin:16px 0 8px;font-size:15px;border-left:3px solid #c8102e;padding-left:8px;}
table{width:100%;border-collapse:collapse;margin:8px 0 4px;font-size:12px;}
th,td{border:1px solid #e3e3e3;padding:6px 8px;text-align:center;}
th{background:#f6f6f6;color:#111111;font-weight:700;}
tr:nth-child(even) td{background:#fcfcfc;}
.method{max-width:880px;}
.mt td,.mt th{text-align:left;}
.ok{color:#8a8a8a;} .na{color:#c8102e;} .badge{display:inline-block;background:#fafafa;border:1px solid #e3e3e3;border-radius:8px;padding:8px 12px;margin:6px 0;font-size:12px;}
.badge.warn{border-color:#c8102e;background:#fdf2f3;color:#c8102e;}
.badge.ok{border-color:#d8d8d8;background:#f6f6f6;color:#444;}
.quote{margin:12px 0;padding:10px 14px;background:#fafafa;border-left:3px solid #c8102e;color:#444;font-style:italic;}
.disclaimer{margin:20px 16px 36px;padding:12px 14px;background:#fafafa;border:1px solid #e3e3e3;border-radius:10px;color:#666;font-size:12px;line-height:1.7;}
.disclaimer b{color:#c8102e;}
.sub{color:#666;font-size:12px;margin:2px 0 10px;}
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ETF策略信号看板</title>
<style>
%s
</style>
</head>
<body>
<div class="topnav">
  <div class="brand">ETF策略信号看板<small>热门20只ETF · 三类策略信号</small></div>
  <a class="tabbtn" href="rotation.html" style="text-decoration:none">三因子轮动信号</a>
  <div class="datebadge" id="datebadge">📅 行情日期：__DATADATE__</div>
  <div class="tabs">
    <button class="tabbtn active" onclick="showTab('t1',this)">📡 量化交易信号</button>
    <button class="tabbtn" onclick="showTab('t2',this)">🎯 A股短线交易决策</button>
    <button class="tabbtn" onclick="showTab('t3',this)">⚡ A股超短线交易策略</button>
  </div>
</div>

<div id="t1" class="tabpane">
  <div class="sub">① 量化交易信号 —— 对热门20只ETF逐只计算 MACD/RSI/KDJ/均线/布林带，统计多头/空头信号数，给出偏多⭐/偏空/中性判定。红涨绿跌。</div>
  <div class="kpis">%s</div>
  <table><tr><th>代码</th><th>名称</th><th>MACD</th><th>RSI</th><th>KDJ</th><th>均线</th><th>布林带</th><th>综合信号</th></tr>%s</table>
</div>

<div id="t2" class="tabpane" style="display:none">
  <div class="sub">② A股短线交易决策 —— 对热门20只ETF，动量+低位+资金流 加权短线评分(0-100)，风控通过且≥50判为可短线交易。红涨绿跌。</div>
  <div class="kpis">%s</div>
  <table><tr><th>代码</th><th>名称</th><th>动量分</th><th>低位分</th><th>资金流分</th><th>综合分</th><th>判定</th></tr>%s</table>
</div>

<div id="t3" class="tabpane" style="display:none">
  <div class="sub">③ A股超短线交易策略 —— 方法论卡片，并标注对 ETF 的适用边界（红字=不适用，绿字=可迁移）。</div>
  %s
</div>

<div class="disclaimer">⚠️ <b>免责声明：</b>本网站所有内容仅基于历史行情数据的量化模型与策略方法论展示，用于学习与研究，<b>不构成任何投资建议</b>。市场有风险，投资须谨慎，据此操作盈亏自负。</div>

<script>
function showTab(id,btn){
  var ps=document.querySelectorAll('.tabpane');
  for(var i=0;i<ps.length;i++){ps[i].style.display='none';}
  document.getElementById(id).style.display='block';
  var bs=document.querySelectorAll('.tabbtn');
  for(var i=0;i<bs.length;i++){bs[i].classList.remove('active');}
  btn.classList.add('active');
  window.scrollTo(0,0);
}
</script>
</body></html>"""

def main():
    print("== 抓取热门20只ETF 真实K线 ==")
    items = []
    data_date = ""
    for code, name in ETFS:
        try:
            rows = fetch_kline(code, datalen=160)
        except Exception as e:
            print("  [跳过] %s: %s" % (code, e)); continue
        if len(rows) < 65:
            print("  [跳过] %s: 数据不足" % code); continue
        items.append({"code": code, "name": name,
                      "q": analyze_quant(rows), "s": analyze_short(rows)})
        data_date = rows[-1]["day"][:10]
    print("  成功处理 %d 只，行情日期 %s" % (len(items), data_date))

    q_cards, q_rows = render_quant(items)
    s_cards, s_rows = render_short(items)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = TEMPLATE % (CSS, q_cards, q_rows, s_cards, s_rows, METHOD_CARD)
    # 注入日期
    html = html.replace("__DATADATE__", data_date or now[:10])
    html = html.replace("<title>ETF策略信号看板</title>",
                         "<title>ETF策略信号看板 (%s)</title>" % now)

    OUTDIR = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(OUTDIR, exist_ok=True)
    out = os.path.join(OUTDIR, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    # 统计打印
    ln = sum(1 for x in items if x["q"]["verdict"] == "偏多")
    sn = sum(1 for x in items if x["q"]["verdict"] == "偏空")
    rn = sum(1 for x in items if x["q"]["is_range"])
    tn = sum(1 for x in items if x["s"]["tradable"])
    print("== 生成完成 ==")
    print("  量化: 偏多%d / 偏空%d / 震荡%d" % (ln, sn, rn))
    print("  短线: 可交易%d / 观望%d" % (tn, len(items) - tn))
    print("  文件: %s (%d 字节)" % (out, os.path.getsize(out)))

if __name__ == "__main__":
    main()
