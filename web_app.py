import streamlit as st
import pandas as pd
import pandas_ta as ta
import requests
import urllib3
from datetime import datetime, timedelta

# --- 核心設定 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# 設定網頁標題與寬度
st.set_page_config(page_title="AI 股市勝率分析", layout="wide", page_icon="📈")

# --- 抓取資料函數 ---
@st.cache_data(ttl=300) # 加入快取機制，5分鐘內重複查同一支股票不用重新下載
def fetch_history_data(code):
    data_list = []
    try:
        now = datetime.now()
        dates_to_fetch = []
        for i in range(12): # 抓一年資料
            d = now.replace(day=1) - timedelta(days=30*i)
            dates_to_fetch.append(d.strftime('%Y%m01'))
        dates_to_fetch.reverse()
        
        headers = {"User-Agent": "Mozilla/5.0"}
        for date_str in dates_to_fetch:
            url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date_str}&stockNo={code}"
            res = requests.get(url, headers=headers, verify=False, timeout=2)
            js = res.json()
            if 'data' in js:
                for row in js['data']:
                    try:
                        date_parts = row[0].split('/')
                        y = int(date_parts[0]) + 1911
                        m = int(date_parts[1])
                        d = int(date_parts[2])
                        date_val = datetime(y, m, d)
                        h_str = row[4].replace(',', '')
                        l_str = row[5].replace(',', '')
                        c_str = row[6].replace(',', '')
                        if "--" not in c_str:
                            data_list.append({
                                'Date': date_val,
                                'Open': float(row[3].replace(',', '')),
                                'High': float(h_str),
                                'Low': float(l_str),
                                'Close': float(c_str),
                                'Volume': float(row[1].replace(',', ''))
                            })
                    except: pass
    except: pass
    return data_list

# --- 策略分析函數 ---
def audit_strategy(data_list):
    MIN_WIN_RATE = 50
    if len(data_list) < 30: return None
    
    df = pd.DataFrame(data_list)
    df.set_index('Date', inplace=True)
    
    # 指標運算
    df['MA5'] = ta.sma(df['Close'], length=5)
    df['MA20'] = ta.sma(df['Close'], length=20)
    df['RSI'] = ta.rsi(df['Close'], length=14)
    stoch = ta.stoch(df['High'], df['Low'], df['Close'], k=9, d=3, smooth_k=3)
    if stoch is not None:
        df = pd.concat([df, stoch], axis=1)
        k_col = [c for c in df.columns if c.startswith('STOCHk')][0]
        d_col = [c for c in df.columns if c.startswith('STOCHd')][0]
        df['K'] = df[k_col]
        df['D'] = df[d_col]
    else:
        df['K'] = 50; df['D'] = 50
    
    df['Bias'] = ((df['Close'] - df['MA20']) / df['MA20']) * 100
    
    # 布林通道
    bbands = ta.bbands(df['Close'], length=20, std=2)
    if bbands is not None:
        u_col = [c for c in bbands.columns if c.startswith('BBU')][0]
        l_col = [c for c in bbands.columns if c.startswith('BBL')][0]
        df['BB_Upper'] = bbands[u_col]
        df['BB_Lower'] = bbands[l_col]
    else:
        df['BB_Upper'] = 0; df['BB_Lower'] = 0

    # 策略邏輯 (多重指標)
    df['Signal'] = 0
    buy_condition = (
        (df['MA5'] > df['MA20']) & 
        (df['K'] > df['D']) & 
        (df['RSI'] < 80) & 
        (df['Bias'] < 6)
    )
    sell_condition = ((df['Close'] < df['MA20']) | (df['K'] < df['D']))
    
    entry_signal = buy_condition & (~buy_condition.shift(1).fillna(False))
    exit_signal = sell_condition & (~sell_condition.shift(1).fillna(False))
    
    df.loc[entry_signal, 'Signal'] = 1
    df.loc[exit_signal, 'Signal'] = -1
    
    # 回測
    position = 0; entry_price = 0; total_profit = 0; trade_count = 0; win_count = 0
    for i in range(len(df)):
        sig = df['Signal'].iloc[i]
        price = df['Close'].iloc[i]
        if sig == 1 and position == 0:
            position = 1; entry_price = price
        elif sig == -1 and position == 1:
            position = 0; profit = price - entry_price
            total_profit += profit; trade_count += 1
            if profit > 0: win_count += 1
            
    if position == 1:
        floating = df['Close'].iloc[-1] - entry_price
        total_profit += floating; trade_count += 1
        if floating > 0: win_count += 1
        
    win_rate = int((win_count/trade_count)*100) if trade_count > 0 else 0
    
    return {
        "trade_count": trade_count,
        "win_rate": win_rate,
        "total_profit": total_profit,
        "is_buy_signal": buy_condition.iloc[-1],
        "position": position,
        "last_row": df.iloc[-1]
    }

# --- 手機版網頁介面 ---
st.title("📱 AI 股市隨身助理")
st.caption("多重指標回測系統 (MA+KD+RSI+Bias)")

# 輸入區
col_input, col_btn = st.columns([3, 1])
with col_input:
    code = st.text_input("股票代號", "2330", label_visibility="collapsed", placeholder="輸入代號")
with col_btn:
    run_btn = st.button("分析", use_container_width=True)

if run_btn:
    with st.spinner('AI 雲端運算中...'):
        data = fetch_history_data(code)
        
        if not data or len(data) < 30:
            st.error("❌ 找不到資料或上市時間太短")
        else:
            res = audit_strategy(data)
            row = res['last_row']
            price = row['Close']
            
            # 1. 顯示大字報 (股價)
            st.markdown(f"""
            <div style="text-align:center; padding:10px; background-color:#1E1E1E; border-radius:10px; margin-bottom:10px">
                <h1 style="color:#FFFFFF; margin:0">{price}</h1>
                <p style="color:#AAAAAA; margin:0">收盤價</p>
            </div>
            """, unsafe_allow_html=True)

            # 2. 顯示建議
            if res['is_buy_signal']:
                if res['win_rate'] >= 50:
                    st.success(f"🔥 強力推薦！(勝率 {res['win_rate']}% | 預期獲利 {res['total_profit']:.1f})")
                else:
                    st.warning(f"⚠️ 條件符合但風險高 (勝率僅 {res['win_rate']}%)")
            elif res['position'] == 1:
                st.info("🔵 持倉續抱中 (尚未出現賣訊)")
            else:
                st.error("⏸️ 暫不建議進場 (觀望)")

            # 3. 關鍵指標
            c1, c2, c3 = st.columns(3)
            c1.metric("勝率", f"{res['win_rate']}%")
            c2.metric("KD", f"K{row['K']:.0f}")
            c3.metric("RSI", f"{row['RSI']:.0f}")

            # 4. 詳細數據 (折疊式)
            with st.expander("📊 查看詳細技術指標", expanded=True):
                st.write(f"**MA 排列**: {'多頭' if row['MA5']>row['MA20'] else '空頭/盤整'} (MA20: {row['MA20']:.2f})")
                st.write(f"**KD 狀態**: {'金叉向上' if row['K']>row['D'] else '死叉向下'} (K{row['K']:.1f}/D{row['D']:.1f})")
                st.write(f"**乖離率**: {row['Bias']:.2f}% {'(過大)' if abs(row['Bias'])>5 else '(健康)'}")
                st.write(f"**布林通道**: {row['BB_Upper']:.2f} ~ {row['BB_Lower']:.2f}")
                st.divider()
                st.caption(f"回測樣本數: {res['trade_count']} 次 | 策略總損益: {res['total_profit']:.1f}")