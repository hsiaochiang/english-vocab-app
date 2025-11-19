import streamlit as st
import pdfplumber
import pandas as pd
import re
from gtts import gTTS
from pydub import AudioSegment
import io
import os

# 設定頁面配置
st.set_page_config(page_title="會考英文單字聽力生成器 v3.0", layout="wide")

# --- 核心功能 1: 解析 PDF (v3 強力版) ---
@st.cache_data
def parse_pdf(pdf_path):
    """
    解析會考單字 PDF。
    v3修正：加入 'Stream' 模式 fallback，解決無格線表格無法讀取的問題。
    """
    data = []
    debug_logs = [] 
    
    if not os.path.exists(pdf_path):
        return pd.DataFrame(), ["錯誤：找不到 PDF 檔案"]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            debug_logs.append(f"PDF 共有 {total_pages} 頁")

            for p_idx, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                
                # 1. 抓取頻率 (出現次數)
                current_freq = 0
                freq_match = re.search(r'出現次數\s*[:：]\s*(\d+)', text)
                if freq_match:
                    current_freq = int(freq_match.group(1))
                
                # 2. 提取表格 - 策略 A: 預設 (找格線)
                tables = page.extract_tables()
                
                # 3. 提取表格 - 策略 B: Stream (找空白間距)
                # 如果策略 A 失敗，改用策略 B
                if not tables:
                    # vertical_strategy="text" 會根據文字的垂直對齊來猜測欄位
                    tables = page.extract_tables(table_settings={
                        "vertical_strategy": "text", 
                        "horizontal_strategy": "text",
                        "snap_tolerance": 5
                    })
                    if tables:
                        debug_logs.append(f"頁面 {p_idx+1}: 啟用強力模式 (Stream Mode) 成功抓取表格")
                
                if not tables:
                    if "出現次數" in text:
                         debug_logs.append(f"頁面 {p_idx+1}: 仍無法偵測到表格 (跳過)")
                    continue

                # 4. 處理抓到的表格內容
                for table in tables:
                    for row in table:
                        # 清理 row (移除 None 和換行)
                        row = [str(cell).replace('\n', ' ').strip() if cell is not None else "" for cell in row]
                        
                        # 跳過太短或全空的行
                        if not any(row) or len(row) < 2: 
                            continue

                        word = ""
                        definition = ""
                        
                        # 尋找定義欄位 (通常包含詞性 [v.] [n.] 等)
                        def_index = -1
                        for i, cell in enumerate(row):
                            # 寬鬆匹配詞性標記
                            if re.search(r'\[\s*(v\.|n\.|adj\.|adv\.|prep\.|conj\.|pron\.|aux\.|art\.|num\.|缩写|縮寫)', cell, re.IGNORECASE):
                                def_index = i
                                definition = cell
                                break
                        
                        # 如果找到定義
                        if def_index > 0:
                            # 嘗試在定義欄位的「左側」尋找單字
                            # 往左搜尋直到找到看起來像單字的欄位
                            potential_word = ""
                            for j in range(def_index - 1, -1, -1):
                                txt = row[j]
                                # 檢查是否為純英文 (允許連字號和空格) 且長度大於1
                                if re.match(r'^[a-zA-Z\-\s\.]+$', txt) and len(txt) > 1:
                                    potential_word = txt
                                    break
                            
                            if potential_word:
                                word = potential_word.strip()
                                
                                # 提取年份 (尋找 05-14)
                                full_row_text = " ".join(row)
                                years_found = re.findall(r'\b(0[5-9]|1[0-4])\b', full_row_text)
                                years_list = [int(y) + 100 for y in years_found]
                                years_list = sorted(list(set(years_list)))
                                
                                data.append({
                                    "Word": word,
                                    "Definition": definition,
                                    "Frequency": current_freq,
                                    "Years": years_list,
                                    "Year_Str": ", ".join(map(str, years_list)) if years_list else "-"
                                })
            
            debug_logs.append(f"解析完成，共提取 {len(data)} 個單字")
            
    except Exception as e:
        return pd.DataFrame(), [f"發生未預期的錯誤: {str(e)}"]

    return pd.DataFrame(data), debug_logs

# --- 核心功能 2: 合併音訊 ---
def combine_audio(playlist_df, silence_duration):
    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=silence_duration * 1000)
    
    progress_text = "正在合成語音... (請勿關閉視窗)"
    my_bar = st.progress(0, text=progress_text)
    total = len(playlist_df)
    
    for i, row in playlist_df.iterrows():
        word = row['Word']
        try:
            tts = gTTS(text=word, lang='en')
            mp3_fp = io.BytesIO()
