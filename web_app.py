import streamlit as st
import pandas as pd
import pandas_ta as ta
import requests
import urllib3
import json
import os
import math
import time
from datetime import datetime, timedelta

# --- 1. 核心設定 ---
st.set_page_config(page_title="股市勝率分析助手", page_icon="📈", layout="wide")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 修正 requests SSL
old_request = requests.Session.request
def new_request(self, method, url, *args, **kwargs):
    kwargs['verify'] = False
    return old_request(self, method, url, *args, **kwargs)
requests.Session.request = new_request

# --- 2. 檔案存取 (Watchlist) ---
# 在 Streamlit Cloud 上，因為無法永久存檔，我們改用 Session State 暫存
# 如果需要永久存檔，需要連接 Google Sheets 或資料庫，這裡先做簡易版
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = {"2330": "台積電"}

# --- 3. 資料抓取函數 (快取優化) ---
@st.cache_data(ttl=3600) # 設定快取 1 小時，避免重複一直抓
def fetch_history_data(code):
    data_list = []
    try:
        now = datetime.now()
        dates_to_fetch = []
        for i in range(12): # 抓近12個月
            d = now.replace(day=1) - timedelta(days=30*i)
            dates_to_fetch.append(d.strftime('%Y%m01'))
        dates_to_fetch.reverse()
        
        headers = {"User-Agent": "Mozilla/5.0"}
        
        for date_str in dates_to_fetch:
            url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date_str}&stockNo={code}"
            res = requests.get(url, headers=headers, verify=False, timeout=5)
            js = res.json()
            if 'data' in js:
                for row in js['data']:
                    try:
                        date_parts = row[0].split('/')
                        y = int(date_parts[0]) + 1911
                        m = int(date_parts[1])
                        d = int(date_parts[2])
                        date_val = datetime(y, m, d)
                        c_str = row[6].replace(',', '')
                        if "--" not in c_str:
                            data_list.append({
                                'Date': date_val,
                                'Open': float(row[3].replace(',', '')),
                                'High': float(row[4].replace(',', '')),
                                'Low': float(row[5].replace(',', '')),
                                'Close': float(c_str),
                                'Volume': float(row[1].replace(',', ''))
                            })
                    except: pass
            time.sleep(0.1) 
    except Exception as e:
        print(f"Error: {e}")
    return data_list

def get_realtime_price(code):
    try:
        import twstock
        rt = twstock.realtime.get(code)
        if rt['success']:
            latest = rt['realtime']['latest_trade_price']
            if latest == '-' and rt['realtime']['best_bid_price']:
                latest = rt['realtime']['best_bid_price'][0]
            if latest != '-':
                return float(latest)
    except: pass
    return None

# --- 4. 策略分析邏輯 ---
def run_strategy_analysis(code, name):
    hist_list = fetch_history_data(code)
    
    # 補上即時資料 (如果有的話)
    current_price = get_realtime_price(code)
    if current_price:
        hist_list.append({
            'Date': datetime.now(),
            'Close': current_price, 'Open': current_price,
            'High': current_price, 'Low': current_price, 'Volume': 0
        })

    if len(hist_list) < 30:
        return None, "資料不足", 0

    df = pd.DataFrame(hist_list)
    df.set_index('Date', inplace=True)

    # 指標計算
    df['MA5'] = ta.sma(df['Close'], length=5)
    df['MA20'] = ta.sma(df['Close'], length=20)
    df['RSI'] = ta.rsi(df['Close'], length=14)
    stoch = ta.stoch(df['High'], df['Low'], df['Close'], k=9, d=3, smooth_k=3)
    if stoch is not None:
        df = pd.concat([df, stoch], axis=1)
        k_col = [c for c in df.columns if c.startswith('STOCHk')][0]
        d_col = [c for c in df.columns if c.startswith('STOCHd')][0]
        df['K'] = df[k_col]; df['D'] = df[d_col]
    else: df['K'] = 50; df['D'] = 50
    
    df['Bias'] = ((df['Close'] - df['MA20']) / df['MA20']) * 100
    
    # 回測邏輯
    df['Signal'] = 0
    buy_cond = ((df['MA5'] > df['MA20']) & (df['K'] > df['D']) & (df['RSI'] < 80) & (df['Bias'] < 6))
    sell_cond = ((df['Close'] < df['MA20']) | (df['K'] < df['D']))
    
    entry_signal = buy_cond & (~buy_cond.shift(1).fillna(False))
    exit_signal = sell_cond & (~sell_cond.shift(1).fillna(False))
    
    df.loc[entry_signal, 'Signal'] = 1
    df.loc[exit_signal, 'Signal'] = -1
    
    # 計算勝率
    position = 0; entry_price = 0; win_count = 0; trade_count = 0
    for i in range(len(df)):
        sig = df['Signal'].iloc[i]
        p = df['Close'].iloc[i]
        if sig == 1 and position == 0:
            position = 1; entry_price = p
        elif sig == -1 and position == 1:
            position = 0; trade_count += 1
            if p > entry_price: win_count += 1
    
    if position == 1:
        trade_count += 1
        if df['Close'].iloc[-1] > entry_price: win_count += 1
        
    win_rate = int((win_count/trade_count)*100) if trade_count > 0 else 0
    return df, win_rate, current_price

# --- 5. 介面佈局 ---

# 側邊欄：股票清單
with st.sidebar:
    st.header("📋 自選股清單")
    
    # 新增股票
    c1, c2 = st.columns([2, 1])
    new_code = c1.text_input("股票代號", placeholder="2330", label_visibility="collapsed")
    if c2.button("加入"):
        if new_code:
            import twstock
            if new_code not in st.session_state.watchlist:
                try:
                    name = twstock.codes[new_code].name
                    st.session_state.watchlist[new_code] = name
                    st.success(f"已加入 {name}")
                    st.rerun()
                except: st.error("無效代號")

    # 顯示清單
    selected_code = st.radio(
        "選擇股票進行分析：",
        options=list(st.session_state.watchlist.keys()),
        format_func=lambda x: f"{x} {st.session_state.watchlist[x]}"
    )
    
    if st.button("❌ 刪除此股票"):
        del st.session_state.watchlist[selected_code]
        st.rerun()

# 主畫面
if selected_code:
    name = st.session_state.watchlist[selected_code]
    st.title(f"{name} ({selected_code})")
    
    # 執行分析
    with st.spinner(f"正在分析 {name} 的歷史數據與籌碼..."):
        df, win_rate, now_price = run_strategy_analysis(selected_code, name)

    if df is not None:
        last = df.iloc[-1]
        
        # 頂部資訊卡
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("現價", f"{last['Close']}", delta=f"{last['Close']-df['Open'].iloc[-1]:.2f}")
        col2.metric("歷史勝率", f"{win_rate}%", help="過去一年符合策略的獲利機率")
        col3.metric("KD 指標", f"K{last['K']:.1f}", f"D{last['D']:.1f}")
        col4.metric("乖離率", f"{last['Bias']:.2f}%", "正乖離過大需小心" if last['Bias']>5 else "正常")

        # 分頁功能
        tab1, tab2 = st.tabs(["📊 AI 策略分析", "💰 損益試算 (含稅費)"])

        # --- Tab 1: AI 分析報告 ---
        with tab1:
            st.subheader("多重指標綜合評估")
            
            # 判斷訊號
            ma_ok = last['MA5'] > last['MA20']
            kd_ok = last['K'] > last['D']
            rsi_ok = last['RSI'] < 80
            
            cond_text = ""
            cond_text += "✅ 均線多頭排列 (短線 > 長線)\n" if ma_ok else "❌ 均線目前偏弱\n"
            cond_text += "✅ KD 黃金交叉 (動能向上)\n" if kd_ok else "❌ KD 死亡交叉 (動能向下)\n"
            cond_text += "✅ RSI 指標健康 (未過熱)\n" if rsi_ok else "⚠️ RSI 過熱 (可能拉回)\n"
            
            st.text_area("策略詳情", cond_text, height=150)
            
            st.line_chart(df[['Close', 'MA20']])
            st.caption("藍線: 收盤價 / 紅線: 月線 (MA20)")

        # --- Tab 2: 損益試算 (含 AI 建議) ---
        with tab2:
            st.write("### 交易成本與損益試算")
            
            c_input1, c_input2 = st.columns(2)
            
            # 使用 session_state 來儲存建議值
            if 'calc_price' not in st.session_state: st.session_state.calc_price = now_price
            if 'calc_profit_pct' not in st.session_state: st.session_state.calc_profit_pct = 10.0
            if 'calc_loss_pct' not in st.session_state: st.session_state.calc_loss_pct = 5.0
            
            # AI 建議按鈕 logic
            if st.button("🤖 載入 AI 停損建議 (MA20)"):
                ma20 = last['MA20']
                if now_price and now_price > ma20:
                    suggested_loss =
