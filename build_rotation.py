# -*- coding: utf-8 -*-
"""
ETF 三因子轮动信号 生成器（纯标准库，可部署 GitHub Actions 每日自动更新）

模型：M 动量 40% + V 估值低位 30% + F 资金流 30%
  综合分 = (40*M + 30*V + 30*F) / 100，各因子取值 -100 ~ +100
标的池：同花顺热榜（概念榜 + 行业榜）对应 ETF，按热度去重排序取前 20
数据：同花顺热榜接口 + 新浪财经日 K 线（250 日）
输出：单文件离线 rotation.html（黑 / 白 / 红 三色，不引外链，断网可用）

口径说明（重要）：
  原 V 因子给板块/个股用的是「PB + 破净率」，ETF 没有 PB 也不存在破净率，
  故 V 改用「近 250 日价格区间位置分位」代理估值分位（位置越低 → 越便宜 → 分越高），
  并叠加「区间位置越高越拥挤」的反向含义。这是代理指标，用于相对排序。
"""
import os
import sys
import ssl
import json
import time
import datetime
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}
THS_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://eq.10jqka.com.cn/",
}

# 热榜抓取失败时的备用标的池
FALLBACK_ETFS = [
    ("sh510300", "沪深300ETF"), ("sh510500", "中证500ETF"), ("sz159915", "创业板ETF"),
    ("sh518880", "黄金ETF"), ("sh512100", "中证1000ETF"), ("sz159919", "沪深300ETF"),
    ("sh510050", "上证50ETF"), ("sz159949", "创业板50ETF"), ("sh512660", "军工ETF"),
    ("sh512480", "半导体ETF"), ("sz159995", "芯片ETF"), ("sh512760", "芯片ETF"),
    ("sh515030", "新能源ETF"), ("sz159770", "机器人ETF"), ("sh588000", "科创50ETF"),
    ("sz159892", "恒生医药ETF"), ("sh513180", "恒生科技ETF"), ("sz159920", "恒生ETF"),
    ("sh512010", "医药ETF"), ("sh512690", "酒ETF"),
]


def http_get_json(url, headers, timeout=15):
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_kline(symbol, datalen=250, retry=2):
    """新浪日 K 线，返回按日期升序的 [{day, open, high, low, close, volume}]"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (symbol, datalen))
    last_err = None
    for _ in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=SINA_HEADERS)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                raw = resp.read().decode("gbk", errors="ignore")
            data = json.loads(raw)
            if not data:
                return []
            rows = [{"day": d["day"], "open": float(d["open"]), "high": float(d["high"]),
                     "low": float(d["low"]), "close": float(d["close"]),
                     "volume": float(d["volume"])} for d in data]
            rows.sort(key=lambda x: x["day"])
            return rows
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    print("    [K线] %s 抓取失败: %s" % (symbol, repr(last_err)[:120]))
    return []


def fetch_ths_hot20():
    """同花顺热榜：概念 + 行业合并，按热度(rate)去重排序取前 20，返回 [(code, name, plate)]"""
    base = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate"
    seen = {}
    for typ in ("concept", "industry"):
        try:
            j = http_get_json(base + "?type=" + typ, THS_HEADERS)
            for it in j.get("data", {}).get("plate_list", []):
                code = str(it.get("etf_product_id", "") or "").strip()
                if not code:
                    continue
                mid = str(it.get("etf_market_id") or it.get("market_id") or "")
                if mid == "20":
                    pref = "sh"
                elif mid == "36":
                    pref = "sz"
                else:
                    pref = "sh" if code[0] in "56" else "sz"
                try:
                    rate = float(it.get("rate") or 0)
                except Exception:
                    rate = 0.0
                key = pref + code
                rec = {"code": key, "name": it.get("etf_name", "") or it.get("name", ""),
                       "plate": it.get("name", ""), "rate": rate,
                       "tag": it.get("hot_tag", "")}
                if key not in seen or rate > seen[key]["rate"]:
                    seen[key] = rec
        except Exception as e:
            print("  [热榜] %s 抓取失败: %s" % (typ, repr(e)[:120]))
    uniq = sorted(seen.values(), key=lambda x: x["rate"], reverse=True)[:20]
    if not uniq:
        print("  [热榜] 为空，回退备用 ETF 池")
        return [(c, n, "") for c, n in FALLBACK_ETFS]
    print("  同花顺热榜前 %d（按热度）：" % len(uniq))
    for i, t in enumerate(uniq, 1):
        print("   %2d %s(%s) 板块:%s 热度%.0f %s" % (i, t["name"], t["code"], t["plate"], t["rate"], t["tag"]))
    return [(t["code"], t["name"], t["plate"]) for t in uniq]


# ===================== 三因子打分（全部可机械复算） =====================
def score_m(r20):
    """M 动量：近 20 日涨幅(%) → -100~+100"""
    if r20 >= 5:
        return min(100.0, 80 + (r20 - 5) * 4)
    if r20 >= 0:
        return 20 + r20 * 12
    if r20 >= -5:
        return r20 * 2
    return max(-100.0, -50 + (r20 + 5) * 5)


def score_v(pct):
    """V 估值低位：近 250 日价格区间位置分位(0=最低,100=最高) → -100~+100"""
    return 100 - 2 * pct


def score_f(f_ratio, chg1):
    """F 资金流：5日均量/20日均量 + 当日涨跌 → -100~+100"""
    if chg1 <= -1.5 and f_ratio >= 1.3:          # 放量杀跌 = 资金出逃
        return max(-100.0, -45 - (f_ratio - 1.3) * 60)
    if f_ratio >= 1.2:                            # 温和放量
        return min(100.0, 60 + (f_ratio - 1.2) * 80)
    if f_ratio >= 0.8:                            # 走平
        return (f_ratio - 1.0) * 100
    return max(-80.0, -40 + (f_ratio - 0.8) * 100)  # 缩量


def signal_of(s):
    if s >= 50:
        return "强配", "15–20%", "b-strong"
    if s >= 15:
        return "可配", "10–15%", "b-ok"
    if s >= -15:
        return "中性 / 左侧试探", "5–8%", "b-mid"
    if s >= -50:
        return "观望 / 回避", "0–3%", "b-bad"
    return "回避", "0", "b-bad"


def analyze(rows):
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    n = len(closes)
    cur = closes[-1]
    r20 = (cur / closes[-21] - 1) * 100 if n > 20 else (cur / closes[0] - 1) * 100
    chg1 = (cur / closes[-2] - 1) * 100 if n > 1 else 0.0
    win = closes[-250:] if n >= 250 else closes
    lo, hi = min(win), max(win)
    pct = (cur - lo) / (hi - lo) * 100 if hi > lo else 50.0
    s20 = sum(vols[-20:])
    f_ratio = (sum(vols[-5:]) / 5) / (s20 / 20) if n >= 20 and s20 > 0 else 1.0
    m = score_m(r20)
    v = score_v(pct)
    f = score_f(f_ratio, chg1)
    total = (40 * m + 30 * v + 30 * f) / 100.0
    return {"r20": r20, "chg1": chg1, "pct": pct, "f_ratio": f_ratio,
            "m": m, "v": v, "f": f, "total": total, "close": cur}


# ===================== HTML（黑 / 白 / 红） =====================
CSS = """
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#ffffff;color:#111111;font-family:-apple-system,'Microsoft YaHei',Segoe UI,sans-serif;font-size:13px;line-height:1.65;}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 50px;}
h1{font-size:22px;font-weight:800;letter-spacing:.5px;}
h1 .bar{display:inline-block;width:6px;height:20px;background:#c8102e;vertical-align:-2px;margin-right:8px;}
.meta{color:#666;font-size:12px;margin:6px 0 16px;}
.line{height:2px;background:#111;margin:10px 0 18px;}
.line i{display:block;width:80px;height:2px;background:#c8102e;}
.card{border:1px solid #e3e3e3;border-radius:10px;padding:14px 16px;margin:14px 0;background:#fff;}
h2{font-size:15px;margin:0 0 10px;padding-left:9px;border-left:4px solid #c8102e;}
h3{font-size:13px;color:#111;margin:14px 0 6px;}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 14px;}
.kpi{flex:1;min-width:130px;border:1px solid #e3e3e3;border-radius:10px;padding:12px;text-align:center;background:#fafafa;}
.kpi-n{font-size:26px;font-weight:800;}
.kpi-l{font-size:11.5px;color:#666;margin-top:3px;}
.red{color:#c8102e;} .gray{color:#666;} .blk{color:#111;}
table{width:100%;border-collapse:collapse;font-size:12px;}
th,td{padding:6px 7px;border-bottom:1px solid #ebebeb;text-align:left;vertical-align:middle;}
th{background:#f6f6f6;color:#111;font-weight:700;border-bottom:1px solid #d8d8d8;}
tr:hover td{background:#fcfcfc;}
.num{text-align:right;font-variant-numeric:tabular-nums;}
.code{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#666;}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700;border:1px solid;}
.b-strong{background:#c8102e;color:#fff;border-color:#c8102e;}
.b-ok{background:#fff;color:#c8102e;border-color:#c8102e;}
.b-mid{background:#f2f2f2;color:#444;border-color:#d8d8d8;}
.b-bad{background:#fff;color:#888;border-color:#c9c9c9;}
.bar{position:relative;height:18px;background:#f2f2f2;border-radius:3px;overflow:hidden;}
.bar .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:#cfcfcf;}
.bar .fill{position:absolute;top:0;bottom:0;border-radius:2px;}
.note{font-size:11.5px;color:#666;margin-top:8px;line-height:1.7;}
ul{margin:6px 0;padding-left:20px;} li{margin:3px 0;font-size:12.5px;}
.box{background:#fafafa;border:1px solid #e3e3e3;border-radius:8px;padding:10px 12px;font-size:12.5px;}
.disc{font-size:11.5px;color:#888;margin-top:16px;line-height:1.8;border-top:1px solid #ebebeb;padding-top:12px;}
@media (max-width:640px){.wrap{padding:14px 10px 36px;} table{font-size:11px;} h1{font-size:19px;}}
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ETF 三因子轮动信号 · __DATADATE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">

  <h1><i class="bar"></i>ETF 三因子轮动信号</h1>
  <div class="meta">
    行情日期 <b>__DATADATE__</b> ｜ 标的池：同花顺热榜前 __N__ 只（概念榜+行业榜按热度去重）｜
    模型 M 动量 40% + V 估值低位 30% + F 资金流 30%<br>
    本页由 GitHub Actions 每个交易日自动生成 · 单文件离线 · 不联网 · 数据不出本机
  </div>
  <div class="line"><i></i></div>

  <div class="card">
    <h2>结论先行</h2>
    __KPI__
    <div style="margin-top:12px;font-size:13px">__VERDICT__</div>
  </div>

  <div class="card">
    <h2>综合分排名</h2>
    <div class="note" style="margin-bottom:10px">
      <span class="red">■</span> 正分（红，看多，向右）　
      <span class="gray">■</span> 负分（灰，看空，向左）　｜　中轴为 0 分
    </div>
    __BARS__
    <div class="note">条形长度 = 综合分（−100 ~ +100）。综合分 =（40×M + 30×V + 30×F）÷ 100。</div>
  </div>

  <div class="card">
    <h2>三因子打分明细</h2>
    <table>
      <thead><tr>
        <th>#</th><th>ETF</th><th>代码</th><th>所属板块</th>
        <th class="num">今日%</th><th class="num">20日%</th>
        <th class="num">M 动量</th><th class="num">V 估值</th><th class="num">F 资金</th>
        <th class="num">综合分</th><th>信号</th><th class="num">权益仓位</th>
      </tr></thead>
      <tbody>__ROWS__</tbody>
    </table>
    <div class="note">
      仓位为「占权益仓位」的框架建议，非个性化，须结合个人风险承受与总仓位。
      同一板块多只上榜时，优先看成交额更大的那只（流动性风险更低）。
    </div>
  </div>

  <div class="card">
    <h2>因子口径（自动复算规则）</h2>
    <table>
      <thead><tr><th style="width:90px">因子</th><th>计算方式（全部由日 K 线机械算出，无人工干预）</th></tr></thead>
      <tbody>
        <tr><td><b>M 动量</b><br><span class="code">权重 40%</span></td>
            <td>近 20 日涨幅 r20：r20≥+5% → 80~100；0~+5% → 20~80；−5%~0% → −10~0；&lt;−5% → −50~−100</td></tr>
        <tr><td><b>V 估值低位</b><br><span class="code">权重 30%</span></td>
            <td>近 250 日价格区间位置分位 pct（0=一年最低，100=一年最高），<b>V = 100 − 2×pct</b>。位置越低分越高（越便宜），位置越高分越低（越拥挤）。</td></tr>
        <tr><td><b>F 资金流</b><br><span class="code">权重 30%</span></td>
            <td>量能比 = 近5日均量 ÷ 近20日均量。放量 → 正分；走平 → 0 附近；缩量 → 负分；<b>放量且当日跌超1.5% → 重罚（资金出逃）</b>。</td></tr>
      </tbody>
    </table>
    <div class="box" style="margin-top:10px">
      <b>为什么 V 用「价格位置」而不是「估值分位」：</b>ETF 没有 PB、也不存在破净率，
      跟踪指数的估值分位需要付费终端才能批量取得。价格位置分位可由公开 K 线算出，
      <b>用于相对排序够用，用于绝对估值定价不够</b>——这是代理指标，不是真实估值。
    </div>
  </div>

  <div class="card">
    <h2>风险提示</h2>
    <ul>
      <li>打分是<b>相对的</b>，回答「这批里选谁」，不回答「现在该不该满仓」。</li>
      <li>轮动是<b>结果不是预测</b>。今天强不代表明天强，模型不具备预测能力。</li>
      <li>V 因子为价格位置代理，<b>基本面恶化导致的低位会被误判为便宜</b>（价值陷阱）。</li>
      <li>热榜标的是<b>人气榜</b>，波动与换手通常偏高，注意流动性冲击与滑点。</li>
      <li>本页为框架演示与信息整理，<b>不构成投资建议</b>。</li>
    </ul>
  </div>

  <div class="disc">
    数据来源：同花顺热榜接口（板块热度与对应 ETF）、新浪财经日 K 线（250 日）。
    抓取时间为每个交易日收盘后；若某只标的抓取失败会自动跳过，不影响其余标的。
    所有计算规则已在本页公开，可用任意行情终端复算。
  </div>

</div>
</body>
</html>
"""


def fmt(v, digits=0):
    s = ("%." + str(digits) + "f") % v
    return ("+" + s) if v > 0 else s


def cls_of(v):
    return "red" if v >= 0 else "gray"


def build_rows(items):
    html = ""
    for i, x in enumerate(items, 1):
        a = x["a"]
        sig, pos, scls = signal_of(a["total"])
        html += (
            "<tr>"
            "<td>%d</td>"
            "<td><b>%s</b></td>"
            "<td class='code'>%s</td>"
            "<td>%s</td>"
            "<td class='num %s'>%s</td>"
            "<td class='num %s'>%s</td>"
            "<td class='num %s'>%s</td>"
            "<td class='num %s'>%s</td>"
            "<td class='num %s'>%s</td>"
            "<td class='num %s'><b>%s</b></td>"
            "<td><span class='tag %s'>%s</span></td>"
            "<td class='num'>%s</td>"
            "</tr>"
        ) % (i, x["name"], x["code"], x["plate"] or "—",
             cls_of(a["chg1"]), fmt(a["chg1"], 2),
             cls_of(a["r20"]), fmt(a["r20"], 2),
             cls_of(a["m"]), fmt(a["m"]),
             cls_of(a["v"]), fmt(a["v"]),
             cls_of(a["f"]), fmt(a["f"]),
             cls_of(a["total"]), fmt(a["total"]),
             scls, sig, pos)
    return html


def build_bars(items):
    max_abs = max([abs(x["a"]["total"]) for x in items] + [60.0])
    html = ""
    for i, x in enumerate(items, 1):
        s = x["a"]["total"]
        pct = abs(s) / max_abs * 50.0
        col = "#c8102e" if s >= 0 else "#8a8a8a"
        left = 50.0 if s >= 0 else (50.0 - pct)
        html += (
            "<div style='display:flex;align-items:center;gap:8px;margin:4px 0'>"
            "<div style='width:20px;text-align:right;color:#888;font-size:11px'>%d</div>"
            "<div style='width:140px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>%s</div>"
            "<div class='bar' style='flex:1'><div class='mid'></div>"
            "<div class='fill' style='left:%.2f%%;width:%.2f%%;background:%s'></div></div>"
            "<div style='width:40px;text-align:right;font-weight:800;font-size:12px' class='%s'>%s</div>"
            "</div>"
        ) % (i, x["name"], left, pct, col, cls_of(s), fmt(s))
    return html


def build_kpi(items):
    strong = [x for x in items if x["a"]["total"] >= 50]
    ok = [x for x in items if 15 <= x["a"]["total"] < 50]
    mid = [x for x in items if -15 <= x["a"]["total"] < 15]
    bad = [x for x in items if x["a"]["total"] < -15]
    top = items[0]
    tail = items[-1]
    return (
        "<div class='kpis'>"
        "<div class='kpi'><div class='kpi-n red'>%d</div><div class='kpi-l'>强配</div></div>"
        "<div class='kpi'><div class='kpi-n red'>%d</div><div class='kpi-l'>可配</div></div>"
        "<div class='kpi'><div class='kpi-n gray'>%d</div><div class='kpi-l'>中性</div></div>"
        "<div class='kpi'><div class='kpi-n gray'>%d</div><div class='kpi-l'>观望 / 回避</div></div>"
        "</div>"
        "<div class='box'>"
        "<b>最强：</b><span class='red'>%s（%s）</span> 综合分 <span class='red'><b>%s</b></span>　｜　"
        "<b>最弱：</b><span class='gray'>%s（%s）</span> 综合分 <span class='gray'><b>%s</b></span>"
        "</div>"
    ) % (len(strong), len(ok), len(mid), len(bad),
         top["name"], top["code"], fmt(top["a"]["total"]),
         tail["name"], tail["code"], fmt(tail["a"]["total"]))


def build_verdict(items):
    strong = [x for x in items if x["a"]["total"] >= 50]
    bad = [x for x in items if x["a"]["total"] < -15]
    top = items[0]
    parts = []
    if strong:
        parts.append("进入<b>强配</b>档的有 <span class='red'>%d</span> 只：%s。"
                     % (len(strong), "、".join(["<b>%s</b>(%s)" % (x["name"], x["code"]) for x in strong])))
    else:
        parts.append("今日<b>无标的进入强配档</b>（综合分≥+50），最强的 <b>%s</b>(%s) 综合分为 %s。"
                     % (top["name"], top["code"], fmt(top["a"]["total"])))
    if bad:
        parts.append("<b>回避</b>档（综合分<−15）有 %d 只：%s。"
                     % (len(bad), "、".join(["%s(%s)" % (x["name"], x["code"]) for x in bad])))
    parts.append("综合分由 <b>M 近20日动量</b>、<b>V 近250日价格位置</b>、<b>F 量能变化</b> 三项加权合成，"
                 "满分 100。分数只用于<b>这批热门标的之间的相对排序</b>，不代表绝对买卖点。")
    return " ".join(parts)


def main():
    print("== 抓取同花顺热榜 ==")
    etfs = fetch_ths_hot20()

    print("== 抓取 K 线并计算三因子 ==")
    items = []
    data_date = ""
    for code, name, plate in etfs:
        rows = fetch_kline(code, 250)
        if len(rows) < 30:
            print("  跳过 %s(%s)：K线不足(%d)" % (name, code, len(rows)))
            continue
        a = analyze(rows)
        if not data_date or rows[-1]["day"] > data_date:
            data_date = rows[-1]["day"]
        items.append({"code": code, "name": name, "plate": plate, "a": a})
        print("  %-16s %s  M=%6.1f V=%6.1f F=%6.1f  综合=%6.1f"
              % (name, code, a["m"], a["v"], a["f"], a["total"]))
        time.sleep(0.25)

    if not items:
        print("!! 全部标的抓取失败，终止")
        sys.exit(1)

    items.sort(key=lambda x: x["a"]["total"], reverse=True)
    if not data_date:
        data_date = datetime.date.today().isoformat()

    html = (TEMPLATE
            .replace("__CSS__", CSS)
            .replace("__DATADATE__", data_date)
            .replace("__N__", str(len(items)))
            .replace("__KPI__", build_kpi(items))
            .replace("__VERDICT__", build_verdict(items))
            .replace("__BARS__", build_bars(items))
            .replace("__ROWS__", build_rows(items)))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rotation.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("== 已生成 %s（%d 只标的，行情日期 %s）==" % (out, len(items), data_date))


if __name__ == "__main__":
    main()
