#!/usr/bin/env python3
"""每日收报 — 收盘后一条汇总推送。

行动分层设计：需要操作的放最顶上，市场信息放底下。
只输出规则信号（4%法 + PE区间），不做任何预测性建议。
"""

import html
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()

# 基金 → 对应场内ETF（用于估算当日涨跌）
FUND_MAP = [
    ("110020", "沪深300联接", "sh510300", "沪深300ETF"),
    ("001051", "上证50联接", "sh510050", "上证50ETF"),
    ("009051", "中证红利联接", "sh515080", "红利ETF"),
    ("003376", "中债7-10年", None, None),  # 债基无ETF，波动极小
    ("000216", "黄金联接", "sh518880", "黄金ETF"),
    ("017641", "标普500联接", "sh513500", "标普500ETF"),
    ("019172", "纳指100联接", "sh513100", "纳指100ETF"),
]

INDEX_CODES = [
    ("sh000001", "上证指数"),
    ("sz399006", "创业板指"),
]

# PE规则区间（用户知识库）
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
    """腾讯行情接口，返回 {code: {name, price, chg_pct}}"""
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
        code = parts[2]
        try:
            result[code] = {
                "name": parts[1],
                "price": float(parts[3]),
                "prev_close": float(parts[4]),
                "chg": float(parts[31]),
                "chg_pct": float(parts[32]),
            }
        except (ValueError, IndexError):
            continue
    return result


def fetch_pe_zones():
    """蛋卷PE → 各指数规则区间"""
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


def load_pending_signals():
    """从 fund_state.json 读挂起的4%信号"""
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


def build_report(quotes, pe_data, signals):
    cn = datetime.now(timezone(timedelta(hours=8)))
    # 收盘后运行；周末运行显示周五数据
    title = f"📰 每日收报 {cn.strftime('%Y-%m-%d %A')}"

    b = [f"<h2>{html.escape(title)}</h2><hr/>"]

    # ── 第一层：需要行动 ──
    b.append("<h3>🎯 今日操作指令</h3>")
    if signals:
        b.append("<p style='color:#c0392b'><b>🚨 有待确认的4%买入信号（跌出买点）：</b></p><ul>")
        for code, name in signals.items():
            b.append(f"<li><b>{name} ({code})</b> — 规则：有闲钱可单笔加仓100-300元，没有就忽略</li>")
        b.append("</ul>")
    else:
        b.append("<p>✅ <b>无信号触发，今天不需要任何操作。</b></p>")
    b.append("<p style='font-size:13px;color:#555'>📅 定投自动执行中：纳指100/标普500 每日10元自动扣款（QDII按支付宝计划）</p>")
    b.append("<p style='font-size:13px;color:#555'>⏳ 止盈信号：未触发（需盈利≥15%后回撤4%）</p>")

    # ── 第二层：你的基金今日表现（ETF估算）──
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

    # ── 第四层：PE区间（背景）──
    b.append("<h3>📈 PE估值区间（每周一详报）</h3>")
    b.append("<table border='1' cellpadding='5' style='border-collapse:collapse;font-size:14px'>")
    b.append("<tr><th>指数</th><th>PE</th><th>百分位</th><th>规则区间</th></tr>")
    for name, d in pe_data.items():
        emoji, zone = pe_zone(d["pct"])
        pct_str = f"{d['pct']:.1f}%" if d["pct"] is not None else "N/A"
        pe_str = f"{d['pe']:.2f}" if d["pe"] else "N/A"
        b.append(f"<tr><td>{name}</td><td>{pe_str}</td><td>{pct_str}</td><td>{emoji} {zone}</td></tr>")
    b.append("</table>")
    b.append("<p style='color:#888;font-size:12px'>注：本报告只执行你的规则（PE分区+4%法），不含预测。无信号=不操作，这就是纪律。</p>")

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

    signals = load_pending_signals()
    if signals:
        print(f"挂起信号: {list(signals.values())}")

    title, content = build_report(quotes, pe_data, signals)
    send_pushplus(title, content)


if __name__ == "__main__":
    main()
