#!/usr/bin/env python3
"""每日收报 — 收盘后一条汇总推送。

行动分层：需要操作的放最顶上，市场信息放底下当背景。
只输出规则信号（4%法 + PE区间），不做任何预测性建议。
新增：PE换挡警报（跨日对比 pe_state.json）+ 术语白话对照表。
"""

import html
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
PE_STATE_FILE = "pe_state.json"

FUND_MAP = [
    ("110020", "沪深300联接", "sh510300", "沪深300ETF"),
    ("001051", "上证50联接", "sh510050", "上证50ETF"),
    ("009051", "中证红利联接", "sh515080", "红利ETF"),
    ("003376", "中债7-10年", None, None),
    ("000216", "黄金联接", "sh518880", "黄金ETF"),
    ("017641", "标普500联接", "sh513500", "标普500ETF"),
    ("019172", "纳指100联接", "sh513100", "纳指100ETF"),
]

INDEX_CODES = [
    ("sh000001", "上证指数"),
    ("sz399006", "创业板指"),
]

# 换挡含义：跌穿/升破阈值后，规则要求的新动作
CROSS_RULES = {
    (20, "down"): ("🟢 进入加倍定投区", "规则动作：可加倍投该指数（闲钱允许时）"),
    (20, "up"): ("⚪ 离开加倍区", "规则动作：恢复正常定投额度"),
    (40, "down"): ("🔵 进入定投区", "规则动作：可恢复/开始该指数定投"),
    (40, "up"): ("⚪ 离开定投区", "规则动作：停止该指数定投，转入维持"),
    (80, "down"): ("⚪ 离开止盈警惕区", "规则动作：回到维持，不卖"),
    (80, "up"): ("🔴 进入止盈警惕区", "规则动作：考虑3331法分批止盈"),
}


def pe_zone(pct):
    if pct is None:
        return ("❓", "数据异常")
    if pct < 20:
        return ("🟢", "低估·加倍区")
    if pct < 40:
        return ("🔵", "偏低·定投区")
    if pct < 80:
        return ("⚪", "适中·维持区")
    return ("🔴", "高估·止盈警惕")


def fetch_qq_quotes(codes):
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("gbk", errors="ignore")
    result = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        parts = line.split("=")[1].strip('"').split("~")
        if len(parts) < 33:
            continue
        try:
            result[parts[2]] = {
                "name": parts[1],
                "price": float(parts[3]),
                "chg_pct": float(parts[32]),
            }
        except (ValueError, IndexError):
            continue
    return result


def fetch_pe_zones():
    url = "https://danjuanfunds.com/djapi/index_eva/dj"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    items = {i.get("name"): i for i in data.get("data", {}).get("items", [])}
    out = {}
    for name in ["沪深300", "上证50", "中证红利", "标普500", "纳指100"]:
        item = items.get(name)
        if item:
            pct = item.get("pe_percentile")
            out[name] = {"pe": item.get("pe"), "pct": pct * 100 if pct else None}
    return out


def load_last_pe_state():
    if not os.path.exists(PE_STATE_FILE):
        return {}
    try:
        with open(PE_STATE_FILE) as f:
            return json.load(f).get("pe", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_pe_state(pe_data):
    cn = datetime.now(timezone(timedelta(hours=8)))
    state = {"date": cn.strftime("%Y-%m-%d"), "pe": pe_data}
    with open(PE_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_crossings(old, new):
    """跨日对比：任何指数穿过 20/40/80 阈值 → 换挡警报"""
    alerts = []
    for name, nd in new.items():
        od = old.get(name)
        if not od or od.get("pct") is None or nd.get("pct") is None:
            continue
        o, n = od["pct"], nd["pct"]
        for threshold in (20, 40, 80):
            direction = None
            if o >= threshold and n < threshold:
                direction = "down"
            elif o < threshold and n >= threshold:
                direction = "up"
            if direction:
                label, action = CROSS_RULES[(threshold, direction)]
                alerts.append(f"<b>{name}</b> 跌穿{threshold}%线（{o:.1f}%→{n:.1f}%）{label}<br/>"
                              f"<span style='font-size:13px'>{action}</span>")
                break
    return alerts


def detect_watchlist(pe_data):
    """无状态备胎：距离任一阈值 <5 个点 → 换挡预备提醒"""
    watch = []
    for name, d in pe_data.items():
        pct = d.get("pct")
        if pct is None:
            continue
        for t in (20, 40, 80):
            if 0 < abs(pct - t) < 5:
                side = "下方" if pct < t else "上方"
                watch.append(f"{name} {pct:.1f}%（距{t}%线仅{abs(pct-t):.1f}点，{side}）")
                break
    return watch


def load_pending_signals():
    if not os.path.exists("fund_state.json"):
        return {}
    with open("fund_state.json") as f:
        state = json.load(f)
    out = {}
    name_by_code = {c: n for c, n, _, _ in FUND_MAP}
    for code, s in state.items():
        if not isinstance(s, dict):
            continue
        if s.get("pending_signal"):
            out[code] = name_by_code.get(code, code)
    return out


GLOSSARY = [
    ("PE（市盈率）", "价格÷每年利润。PE=20 就是花20块买\"每年帮你赚1块\"的资产。数字越低越便宜"),
    ("PE百分位", "今天的贵贱程度和历史所有日子比排第几。27% = 比历史上73%的时间都便宜"),
    ("🟢加倍区 / 🔵定投区 / ⚪维持区 / 🔴止盈警惕区", "对应PE百分位 <20% / 20-40% / 40-80% / >80%，规则：多投 / 正常投 / 只持有不加 / 考虑分批卖"),
    ("4%买入信号", "你持有的基金比你买入基准价（锚点）跌超4% = 打折了，可考虑单笔补100-300元"),
    ("锚点", "你建仓那天的价格。所有\"跌了百分之几\"都从它算起"),
    ("止盈信号", "赚超15%以后，从最高点往回跌4% = 提醒分批卖出保住利润"),
    ("联接基金 / ETF", "你在支付宝买的是\"联接基金\"（每天按收盘价结算一次）；ETF是它跟踪的场内实时版。收报用ETF估算你当天的涨跌"),
    ("弹药", "特意留着不投的现金，等大跌时用来补仓"),
    ("换挡", "PE百分位穿过某条线（20/40/80%），规则要求的动作随之改变，比如从\"维持\"换到\"定投\""),
]


def build_report(quotes, pe_data, signals, crossings, watch):
    cn = datetime.now(timezone(timedelta(hours=8)))
    title = f"📰 每日收报 {cn.strftime('%Y-%m-%d %A')}"

    b = [f"<h2>{html.escape(title)}</h2><hr/>"]

    # ── 第0层：换挡警报（最高优先级）──
    if crossings:
        b.append("<h3>🚦 PE换挡警报</h3>")
        b.append("<div style='background:#fff3cd;border:1px solid #ffc107;padding:8px;border-radius:6px'>")
        for c in crossings:
            b.append(f"<p style='margin:4px 0'>⚠️ {c}</p>")
        b.append("</div>")

    # ── 第一层：需要行动 ──
    b.append("<h3>🎯 今日操作指令</h3>")
    if signals:
        b.append("<p style='color:#c0392b'><b>🚨 有待确认的4%买入信号（跌出买点）：</b></p><ul>")
        for code, name in signals.items():
            b.append(f"<li><b>{name} ({code})</b> — 规则：有闲钱可单笔加仓100-300元，没有就忽略</li>")
        b.append("</ul>")
    else:
        b.append("<p>✅ <b>无信号触发，今天不需要任何操作。</b></p>")
    b.append("<p style='font-size:13px;color:#555'>📅 定投自动执行中：纳指100/标普500 每日10元自动扣款</p>")
    b.append("<p style='font-size:13px;color:#555'>⏳ 止盈信号：未触发（需盈利≥15%后回撤4%）</p>")
    if watch:
        b.append(f"<p style='font-size:13px;color:#856404'>👀 换挡预备（接近临界线，留意近期周报）：{'；'.join(watch)}</p>")

    # ── 第二层：持仓今日表现 ──
    b.append("<h3>📊 持仓今日估算（按场内ETF）</h3>")
    b.append("<table border='1' cellpadding='5' style='border-collapse:collapse;font-size:14px'>")
    b.append("<tr><th>基金</th><th>参考ETF</th><th>现价</th><th>今日</th></tr>")
    for code, fname, etf, ename in FUND_MAP:
        if etf and etf in quotes:
            q = quotes[etf]
            color = "#c0392b" if q["chg_pct"] < 0 else "#27ae60"
            b.append(f"<tr><td>{fname}</td><td>{ename}</td><td>{q['price']:.3f}</td>"
                     f"<td style='color:{color}'>{q['chg_pct']:+.2f}%</td></tr>")
        else:
            b.append(f"<tr><td>{fname}</td><td colspan='3'>债基·波动极小</td></tr>")
    b.append("</table>")

    # ── 第三层：大盘 ──
    b.append("<h3>🌍 大盘指数</h3><p>")
    for icode, iname in INDEX_CODES:
        if icode in quotes:
            q = quotes[icode]
            arrow = "🔴" if q["chg_pct"] < 0 else "🟢"
            b.append(f"{arrow} {iname} {q['price']:.2f} ({q['chg_pct']:+.2f}%)　")
    b.append("</p>")

    # ── 第四层：PE区间 ──
    b.append("<h3>📈 PE估值区间（每周一详报）</h3>")
    b.append("<table border='1' cellpadding='5' style='border-collapse:collapse;font-size:14px'>")
    b.append("<tr><th>指数</th><th>PE</th><th>百分位</th><th>规则区间</th></tr>")
    for name, d in pe_data.items():
        emoji, zone = pe_zone(d["pct"])
        pct_str = f"{d['pct']:.1f}%" if d["pct"] is not None else "N/A"
        pe_str = f"{d['pe']:.2f}" if d["pe"] else "N/A"
        b.append(f"<tr><td>{name}</td><td>{pe_str}</td><td>{pct_str}</td><td>{emoji} {zone}</td></tr>")
    b.append("</table>")

    # ── 第五层：术语白话对照 ──
    b.append("<h3>📖 术语 ↔ 白话对照</h3>")
    b.append("<table border='1' cellpadding='5' style='border-collapse:collapse;font-size:13px'>")
    b.append("<tr><th style='width:32%'>术语</th><th>白话</th></tr>")
    for term, plain in GLOSSARY:
        b.append(f"<tr><td><b>{term}</b></td><td>{plain}</td></tr>")
    b.append("</table>")
    b.append("<p style='color:#888;font-size:12px'>本报告只执行你的规则（PE分区+4%法），不含预测。无信号=不操作，这就是纪律。</p>")

    return title, "".join(b)


def send_pushplus(title, content):
    if not PUSHPLUS_TOKEN:
        print("[警告] 未配置 PUSHPLUS_TOKEN")
        return False
    payload = json.dumps({"token": PUSHPLUS_TOKEN, "title": title,
                          "content": content, "template": "html"}).encode()
    req = urllib.request.Request("http://www.pushplus.plus/send", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        r = json.loads(resp.read())
        ok = r.get("code") == 200
        print(f"[通知] PushPlus {'推送成功' if ok else str(r)}")
        return ok


def main():
    print("=== 每日收报启动 ===")
    all_codes = [e for _, _, e, _ in FUND_MAP if e] + [c for c, _ in INDEX_CODES]
    try:
        quotes = fetch_qq_quotes(all_codes)
        print(f"行情: {len(quotes)} 个标的")
    except Exception as e:
        print(f"[错误] 行情获取失败: {e}")
        quotes = {}

    try:
        pe_data = fetch_pe_zones()
        print(f"PE: {len(pe_data)} 个指数")
    except Exception as e:
        print(f"[警告] PE获取失败: {e}")
        pe_data = {}

    crossings = detect_crossings(load_last_pe_state(), pe_data)
    if crossings:
        print(f"换挡警报: {len(crossings)} 条")
    watch = detect_watchlist(pe_data)
    if pe_data:
        save_pe_state(pe_data)

    signals = load_pending_signals()
    if signals:
        print(f"挂起信号: {list(signals.values())}")

    title, content = build_report(quotes, pe_data, signals, crossings, watch)
    send_pushplus(title, content)


if __name__ == "__main__":
    main()
