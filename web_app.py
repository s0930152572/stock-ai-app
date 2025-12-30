import customtkinter as ctk
import requests
import urllib3
import threading
import json
import os
import time
import math
from datetime import datetime, timedelta

# --- 分析套件 ---
import pandas as pd
import pandas_ta as ta

# --- 1. 核心設定與 SSL 修正 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
old_request = requests.Session.request
def new_request(self, method, url, *args, **kwargs):
    kwargs['verify'] = False
    return old_request(self, method, url, *args, **kwargs)
requests.Session.request = new_request

# --- 2. 介面外觀設定 ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- 3. 自定義歷史資料抓取 ---
def fetch_history_data(code):
    data_list = []
    try:
        now = datetime.now()
        dates_to_fetch = []
        for i in range(12): # 抓12個月
            d = now.replace(day=1) - timedelta(days=30*i)
            dates_to_fetch.append(d.strftime('%Y%m01'))
        dates_to_fetch.reverse()
        
        headers = {"User-Agent": "Mozilla/5.0"}
        print(f"正在下載 {code} 近一年歷史數據...")

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
            time.sleep(0.2)
    except Exception as e:
        print(f"Fetch Error: {e}")
    return data_list

# --- 4. 策略回測審核邏輯 ---
def audit_strategy(data_list):
    MIN_WIN_RATE = 50
    if len(data_list) < 30:
        return "⚠️ 資料不足", "樣本太少，無法計算勝率", None

    df = pd.DataFrame(data_list)
    df.set_index('Date', inplace=True)
    
    # 指標計算
    df['MA5'] = ta.sma(df['Close'], length=5)
    df['MA10'] = ta.sma(df['Close'], length=10)
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
        df['K'] = 50
        df['D'] = 50
    df['Bias'] = ((df['Close'] - df['MA20']) / df['MA20']) * 100
    bbands = ta.bbands(df['Close'], length=20, std=2)
    if bbands is not None:
        upper_cols = [c for c in bbands.columns if c.startswith('BBU')]
        lower_cols = [c for c in bbands.columns if c.startswith('BBL')]
        if upper_cols and lower_cols:
            df['BB_Upper'] = bbands[upper_cols[0]]
            df['BB_Lower'] = bbands[lower_cols[0]]
        else:
            df['BB_Upper'] = 0; df['BB_Lower'] = 0
    else:
        df['BB_Upper'] = 0; df['BB_Lower'] = 0

    df['Signal'] = 0
    buy_condition = (
        (df['MA5'] > df['MA20']) & (df['K'] > df['D']) & 
        (df['RSI'] < 80) & (df['Bias'] < 6)
    )
    sell_condition = ((df['Close'] < df['MA20']) | (df['K'] < df['D']))
    
    entry_signal = buy_condition & (~buy_condition.shift(1).fillna(False)) 
    exit_signal = sell_condition & (~sell_condition.shift(1).fillna(False))
    
    df.loc[entry_signal, 'Signal'] = 1
    df.loc[exit_signal, 'Signal'] = -1

    position = 0
    entry_price = 0
    total_profit = 0
    trade_count = 0
    win_count = 0

    for i in range(len(df)):
        sig = df['Signal'].iloc[i]
        price = df['Close'].iloc[i]
        if sig == 1 and position == 0:
            position = 1
            entry_price = price
        elif sig == -1 and position == 1:
            position = 0
            profit = price - entry_price
            total_profit += profit
            trade_count += 1
            if profit > 0: win_count += 1
    
    if position == 1:
        floating = df['Close'].iloc[-1] - entry_price
        total_profit += floating
        trade_count += 1
        if floating > 0: win_count += 1

    win_rate = int((win_count/trade_count)*100) if trade_count > 0 else 0
    today_match = buy_condition.iloc[-1]
    last_signal_msg = "🔥 符合買進條件！" if today_match else ("持倉續抱中" if position == 1 else "觀望")
    
    audit_res = ""
    if today_match:
        audit_res = f"✅ 強力推薦 (勝率{win_rate}% | 策略獲利{total_profit:.1f})" if win_rate >= MIN_WIN_RATE else f"⚠️ 條件符合但勝率低 ({win_rate}%)"
    elif position == 1:
        audit_res = f"🔵 持倉中 (目前浮動損益)"
    else:
        audit_res = f"⏸️ 暫不建議進場"

    detail = (
        f"【多重指標回測分析】\n條件: MA多頭 + KD金叉 + RSI健康\n--------------------------\n"
        f"歷史出現次數: {trade_count} 次\n歷史勝率: {win_rate}% \n策略總損益: {total_profit:.2f}\n"
        f"--------------------------\n今日狀態: {last_signal_msg}"
    )
    return audit_res, detail, df.iloc[-1]

# --- 5. 停利停損試算視窗 (含 AI MA20 功能) ---
class CalculatorWindow(ctk.CTkToplevel):
    def __init__(self, master, code, name, current_price=None):
        super().__init__(master)
        self.title(f"💰 損益試算 - {name} ({code})")
        self.geometry("400x600")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.code = code
        self.current_price = current_price

        # 標題
        ctk.CTkLabel(self, text="交易策略試算 (含稅費)", font=("Microsoft JhengHei UI", 18, "bold")).pack(pady=15)

        # 輸入區塊
        frame = ctk.CTkFrame(self)
        frame.pack(pady=10, padx=20, fill="x")

        # 買進價格
        ctk.CTkLabel(frame, text="買進價格:").grid(row=0, column=0, padx=10, pady=10, sticky="e")
        self.entry_cost = ctk.CTkEntry(frame, placeholder_text="輸入股價")
        self.entry_cost.grid(row=0, column=1, padx=10, pady=10)
        
        if current_price:
            self.entry_cost.insert(0, str(current_price))

        # 停利 %
        ctk.CTkLabel(frame, text="預設停利 (%):", text_color="#FF4444").grid(row=1, column=0, padx=10, pady=10, sticky="e")
        self.entry_profit = ctk.CTkEntry(frame)
        self.entry_profit.insert(0, "10")
        self.entry_profit.grid(row=1, column=1, padx=10, pady=10)

        # 停損 %
        ctk.CTkLabel(frame, text="預設停損 (%):", text_color="#00C853").grid(row=2, column=0, padx=10, pady=10, sticky="e")
        self.entry_loss = ctk.CTkEntry(frame)
        self.entry_loss.insert(0, "5")
        self.entry_loss.grid(row=2, column=1, padx=10, pady=10)

        # AI 按鈕 (新增功能)
        self.btn_ai = ctk.CTkButton(frame, text="🤖 載入 AI 建議 (MA20)", 
                                    fg_color="#8E44AD", hover_color="#7D3C98",
                                    height=30, width=180, 
                                    command=self.load_ai_suggestion)
        self.btn_ai.grid(row=3, column=0, columnspan=2, pady=10)

        # 計算按鈕
        ctk.CTkButton(self, text="開始計算", command=self.calculate, height=40, font=("bold", 14)).pack(pady=5)

        # 結果顯示
        self.result_text = ctk.CTkTextbox(self, width=360, height=200, font=("Microsoft JhengHei UI", 14))
        self.result_text.pack(pady=10)
        self.result_text.insert("0.0", "輸入數值或點選 AI 建議...")
        self.result_text.configure(state="disabled")

    def load_ai_suggestion(self):
        # 啟動執行緒去抓資料，避免卡住畫面
        self.result_text.configure(state="normal")
        self.result_text.delete("0.0", "end")
        self.result_text.insert("0.0", "⏳ 正在分析 MA20 月線支撐...\n請稍候...")
        self.result_text.configure(state="disabled")
        threading.Thread(target=self._run_ai_calculation, daemon=True).start()

    def _run_ai_calculation(self):
        try:
            # 取得歷史資料並計算 MA20
            hist_list = fetch_history_data(self.code)
            
            # 如果有現價，手動補上一筆今天的(或是最新的)讓 MA 更準
            if self.current_price:
                # 簡單模擬一筆今日數據
                hist_list.append({
                    'Date': datetime.now(),
                    'Close': self.current_price, 'Open': self.current_price, 
                    'High': self.current_price, 'Low': self.current_price, 'Volume': 0
                })
            
            if len(hist_list) < 20:
                self.update_result_text("❌ 資料不足，無法計算 MA20")
                return

            df = pd.DataFrame(hist_list)
            df['MA20'] = ta.sma(df['Close'], length=20)
            ma20_price = df['MA20'].iloc[-1]
            
            # 取得介面上的買進價 (如果沒填就用現價)
            try:
                cost_price = float(self.entry_cost.get())
            except:
                cost_price = self.current_price if self.current_price else ma20_price

            if pd.isna(ma20_price):
                self.update_result_text("❌ 無法計算 MA20")
                return

            # 計算停損 %
            # 如果現價已經跌破月線，就不能用月線停損 (會變成負數或不合理)
            if cost_price < ma20_price:
                 self.update_result_text(
                     f"⚠️ 警告：目前股價 ({cost_price}) 已跌破月線 ({ma20_price:.2f})！\n"
                     f"此時不適合用月線當停損。\n建議改看前低或自行設定。"
                 )
            else:
                # 停損 % = (1 - MA20/成本) * 100
                suggested_loss_pct = (1 - (ma20_price / cost_price)) * 100
                suggested_loss_pct = round(suggested_loss_pct, 2)
                
                # 更新介面
                self.entry_loss.delete(0, 'end')
                self.entry_loss.insert(0, str(suggested_loss_pct))
                
                msg = (
                    f"✅ AI 建議已載入！\n"
                    f"----------------------\n"
                    f"月線 (MA20) 價格: {ma20_price:.2f}\n"
                    f"建議停損設為: {suggested_loss_pct}%\n"
                    f"----------------------\n"
                    f"已自動填入停損欄位，請按「開始計算」。"
                )
                self.update_result_text(msg)

        except Exception as e:
            self.update_result_text(f"錯誤: {e}")

    def update_result_text(self, text):
        self.result_text.configure(state="normal")
        self.result_text.delete("0.0", "end")
        self.result_text.insert("0.0", text)
        self.result_text.configure(state="disabled")

    def calculate(self):
        try:
            price = float(self.entry_cost.get())
            profit_pct = float(self.entry_profit.get())
            loss_pct = float(self.entry_loss.get())
            
            # 參數 (台股)
            shares = 1000
            fee_rate = 0.001425
            tax_rate = 0.003
            
            # 買進成本
            buy_val = price * shares
            buy_fee = math.floor(buy_val * fee_rate)
            if buy_fee < 20: buy_fee = 20
            total_cost = buy_val + buy_fee
            
            # 停利計算
            target_price = price * (1 + profit_pct / 100)
            sell_val_win = target_price * shares
            sell_fee_win = math.floor(sell_val_win * fee_rate)
            sell_tax_win = math.floor(sell_val_win * tax_rate)
            net_profit = sell_val_win - sell_fee_win - sell_tax_win - total_cost
            
            # 停損計算
            stop_price = price * (1 - loss_pct / 100)
            sell_val_loss = stop_price * shares
            sell_fee_loss = math.floor(sell_val_loss * fee_rate)
            sell_tax_loss = math.floor(sell_val_loss * tax_rate)
            net_loss = sell_val_loss - sell_fee_loss - sell_tax_loss - total_cost
            
            # 顯示結果
            res = (
                f"【成本】 {total_cost:,.0f} 元 (含手續費 {buy_fee})\n"
                f"----------------------------------\n"
                f"🔴 停利賣價: {target_price:.2f}\n"
                f"   (實賺: +{net_profit:,.0f} 元)\n\n"
                f"🟢 停損賣價: {stop_price:.2f}\n"
                f"   (實賠: {net_loss:,.0f} 元)\n"
                f"----------------------------------\n"
                f"*已扣除證交稅(0.3%)與手續費"
            )
            
            self.result_text.configure(state="normal")
            self.result_text.delete("0.0", "end")
            self.result_text.insert("0.0", res)
            self.result_text.configure(state="disabled")
            
        except ValueError:
            self.result_text.configure(state="normal")
            self.result_text.delete("0.0", "end")
            self.result_text.insert("0.0", "❌ 輸入格式錯誤，請輸入數字")
            self.result_text.configure(state="disabled")

# --- 6. 分析視窗 (保持原樣) ---
class AnalysisWindow(ctk.CTkToplevel):
    def __init__(self, master, code, name):
        super().__init__(master)
        self.title(f"{name} ({code}) - 勝率分析")
        self.geometry("450x800")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.label_title = ctk.CTkLabel(self, text=f"正在分析 {name}...", font=("Microsoft JhengHei UI", 18, "bold"))
        self.label_title.pack(pady=15)
        self.textbox = ctk.CTkTextbox(self, width=410, height=700, font=("Microsoft JhengHei UI", 14))
        self.textbox.pack(pady=10)
        self.textbox.insert("0.0", "AI 正在運算中...\n請稍候...")
        self.textbox.configure(state="disabled")
        threading.Thread(target=self.run_analysis, args=(code, name), daemon=True).start()

    def run_analysis(self, code, name):
        try:
            import twstock
            hist_list = fetch_history_data(code)
            try:
                rt = twstock.realtime.get(code)
                if rt['success']:
                    latest = rt['realtime']['latest_trade_price']
                    if latest == '-' and rt['realtime']['best_bid_price']:
                        latest = rt['realtime']['best_bid_price'][0]
                    if latest != '-':
                        real_data = {
                            'Date': datetime.now(),
                            'Open': float(rt['realtime']['open']),
                            'High': float(rt['realtime']['high']) if rt['realtime']['high'] != '-' else float(latest),
                            'Low': float(rt['realtime']['low']) if rt['realtime']['low'] != '-' else float(latest),
                            'Close': float(latest), 'Volume': 0
                        }
                        hist_list.append(real_data)
            except: pass

            if len(hist_list) < 30:
                self.update_text("資料不足，無法分析。")
                return
            
            audit_title, audit_detail, last_row = audit_strategy(hist_list)
            if last_row is None:
                self.update_text("分析錯誤")
                return

            k_val = last_row['K'] if not pd.isna(last_row['K']) else 50
            d_val = last_row['D'] if not pd.isna(last_row['D']) else 50
            bias = last_row['Bias'] if not pd.isna(last_row['Bias']) else 0
            
            report = (
                f"【{name} 買點勝率分析】\n==========================\n"
                f"現價: {last_row['Close']}  (MA5: {last_row['MA5']:.2f})\n"
                f"==========================\n\n"
                f"🎯 綜合策略回測:\n{audit_title}\n{audit_detail}\n\n"
                f"📊 技術指標:\n"
                f"1. 均線: {'多頭' if last_row['MA5']>last_row['MA20'] else '盤整/空頭'}\n"
                f"2. KD值: K{k_val:.1f} / D{d_val:.1f}\n"
                f"3. 乖離: {bias:.2f}%\n"
                f"==========================\n⚠️ 僅供參考。"
            )
            self.update_text(report)
        except Exception as e:
            self.update_text(f"錯誤: {e}")

    def update_text(self, text):
        try:
            self.textbox.configure(state="normal")
            self.textbox.delete("0.0", "end")
            self.textbox.insert("0.0", text)
            self.textbox.configure(state="disabled")
        except: pass

# --- 7. 股票卡片 (含試算按鈕) ---
class StockCard(ctk.CTkFrame):
    def __init__(self, master, code, name, delete_callback, analyze_callback):
        super().__init__(master, fg_color="#2B2B2B", corner_radius=10)
        self.code = code
        self.name = name
        self.current_price = None 
        self.delete_callback = delete_callback
        self.analyze_callback = analyze_callback
        self.grid_columnconfigure(1, weight=1)

        self.label_name = ctk.CTkLabel(self, text=f"{name}\n{code}", font=("Microsoft JhengHei UI", 15, "bold"), justify="left")
        self.label_name.grid(row=0, column=0, padx=(15, 5), pady=10, sticky="w")

        self.label_price = ctk.CTkLabel(self, text="Loading...", font=("Arial", 18, "bold"))
        self.label_price.grid(row=0, column=1, padx=5, pady=10)

        self.label_pct = ctk.CTkLabel(self, text="--%", font=("Arial", 14))
        self.label_pct.grid(row=0, column=2, padx=5, pady=10)

        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=0, column=3, padx=(5, 10), pady=10)

        self.btn_calc = ctk.CTkButton(self.btn_frame, text="💲", width=35, height=30,
                                      fg_color="#F39C12", hover_color="#D68910",
                                      command=self.open_calculator)
        self.btn_calc.pack(side="left", padx=2)

        self.btn_analyze = ctk.CTkButton(self.btn_frame, text="📊", width=35, height=30,
                                         fg_color="#3498db", hover_color="#2980b9",
                                         command=lambda: analyze_callback(code, name))
        self.btn_analyze.pack(side="left", padx=2)

        self.btn_del = ctk.CTkButton(self.btn_frame, text="×", width=35, height=30, 
                                     fg_color="#444", hover_color="#C0392B",
                                     command=lambda: delete_callback(self))
        self.btn_del.pack(side="right", padx=2)

    def open_calculator(self):
        CalculatorWindow(self.winfo_toplevel(), self.code, self.name, self.current_price)

    def update_data(self, realtime_data):
        data = realtime_data.get(self.code)
        if data and data['success']:
            latest_price = data['realtime']['latest_trade_price']
            if latest_price == '-' and data['realtime']['best_bid_price']:
                latest_price = data['realtime']['best_bid_price'][0]
            if latest_price != '-':
                price = float(latest_price)
                self.current_price = price
                ref_price = float(data['realtime']['open'])
                change = price - ref_price
                pct = (change / ref_price) * 100
                color = "#FF4444" if change > 0 else ("#00C853" if change < 0 else "#FFFFFF")
                self.label_price.configure(text=f"{price:.2f}", text_color=color)
                self.label_pct.configure(text=f"{change:.2f} ({pct:.2f}%)", text_color=color)
            else:
                self.label_price.configure(text="--", text_color="white")
                self.label_pct.configure(text="暫無交易")

# --- 8. 主程式 ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("我的自選股 APP (v6.0 AI 智能版)")
        self.geometry("520x600") 
        self.resizable(False, True)

        self.data_file = "watchlist.json"
        self.watchlist = self.load_data() 
        self.stock_cards = []

        self.title_label = ctk.CTkLabel(self, text="📈 股市勝率分析助手", font=("Microsoft JhengHei UI", 24, "bold"))
        self.title_label.pack(pady=20)

        self.input_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.input_frame.pack(pady=10, padx=20, fill="x")

        self.entry_code = ctk.CTkEntry(self.input_frame, placeholder_text="輸入代號 (如 2330)")
        self.entry_code.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_add = ctk.CTkButton(self.input_frame, text="加入", width=60, command=self.add_stock)
        self.btn_add.pack(side="right")

        self.scroll_frame = ctk.CTkScrollableFrame(self, width=480, height=400)
        self.scroll_frame.pack(pady=10, padx=10, fill="both", expand=True)

        self.status_label = ctk.CTkLabel(self, text=f"最後更新: {datetime.now().strftime('%H:%M:%S')}", text_color="gray")
        self.status_label.pack(pady=5)

        self.refresh_ui_list()
        self.after(200, self.start_update_loop)

    def load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f: return json.load(f)
            except: return {"2330": "台積電"}
        else: return {"2330": "台積電"}

    def save_data(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.watchlist, f, ensure_ascii=False, indent=4)
        except Exception as e: print(f"存檔失敗: {e}")

    def add_stock(self):
        import twstock 
        code = self.entry_code.get().strip()
        if not code: return
        if code in self.watchlist: return
        try:
            name = twstock.codes[code].name if code in twstock.codes else code
            self.watchlist[code] = name
            self.save_data()
            self.entry_code.delete(0, 'end')
            self.refresh_ui_list()
            self.update_data_once()
        except: pass

    def remove_stock(self, card_instance):
        if card_instance.code in self.watchlist:
            del self.watchlist[card_instance.code]
            self.save_data()
            self.refresh_ui_list()

    def open_analysis(self, code, name):
        AnalysisWindow(self, code, name)

    def refresh_ui_list(self):
        for card in self.stock_cards: card.destroy()
        self.stock_cards.clear()
        for code, name in self.watchlist.items():
            card = StockCard(self.scroll_frame, code, name, self.remove_stock, self.open_analysis)
            card.pack(pady=5, padx=5, fill="x")
            self.stock_cards.append(card)

    def start_update_loop(self):
        threading.Thread(target=self.update_data_once, daemon=True).start()
        self.after(5000, self.start_update_loop)

    def update_data_once(self):
        import twstock
        if not self.watchlist: return
        try:
            codes = list(self.watchlist.keys())
            realtime_data = twstock.realtime.get(codes)
            for card in self.stock_cards: card.update_data(realtime_data)
            self.status_label.configure(text=f"最後更新: {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e: print(f"Update error: {e}")

if __name__ == "__main__":
    app = App()
    app.mainloop()
