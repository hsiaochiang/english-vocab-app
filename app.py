import streamlit as st
import pdfplumber
import pandas as pd
import re
from gtts import gTTS
from pydub import AudioSegment
import io
import os

# 設定頁面配置
st.set_page_config(page_title="會考英文單字聽力生成器", layout="wide")

# --- 核心功能 1: 解析 PDF (修正版) ---
@st.cache_data
def parse_pdf(pdf_path):
    """
    解析會考單字 PDF，提取單字、定義、頻率與年份。
    v2修正：增加容錯率與除錯資訊
    """
    data = []
    debug_logs = [] # 儲存除錯訊息
    
    if not os.path.exists(pdf_path):
        return pd.DataFrame(), ["錯誤：找不到 PDF 檔案"]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            debug_logs.append(f"PDF 共有 {total_pages} 頁")

            for p_idx, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                
                # 1. 抓取「出現次數」
                # 嘗試多種格式: "出現次數:10", "出現次數: 9", "出現次數 : 10"
                current_freq = 0
                freq_match = re.search(r'出現次數\s*[:：]\s*(\d+)', text)
                if freq_match:
                    current_freq = int(freq_match.group(1))
                
                # 2. 提取表格
                # 使用預設設定，如果失敗可能需要調整 vertical_strategy
                tables = page.extract_tables()
                
                if not tables:
                    # 如果這一頁有文字但沒抓到表格，紀錄一下
                    if "出現次數" in text:
                        debug_logs.append(f"頁面 {p_idx+1}: 發現關鍵字但未偵測到表格")
                    continue

                for table in tables:
                    for row in table:
                        # 清理 row (移除 None)
                        row = [str(cell).replace('\n', ' ').strip() if cell is not None else "" for cell in row]
                        
                        # 跳過空行
                        if not any(row): 
                            continue

                        word = ""
                        definition = ""
                        
                        # 尋找定義欄位的索引
                        # 修正 Regex: 允許 [ v. ] 這種有空格的情況，並忽略大小寫
                        def_index = -1
                        for i, cell in enumerate(row):
                            if re.search(r'\[\s*(v\.|n\.|adj\.|adv\.|prep\.|conj\.|pron\.|aux\.|art\.|num\.)\s*\]', cell, re.IGNORECASE):
                                def_index = i
                                definition = cell
                                break
                        
                        # 如果找到定義，且前面有欄位 (假設單字在定義欄的前一欄)
                        if def_index > 0:
                            potential_word = row[def_index - 1]
                            # 清理單字: 只保留英文字母、連字號
                            # 排除像是 "P1", "07" 這種頁碼或年份誤判
                            clean_word_match = re.match(r'^[a-zA-Z\-\s]+$', potential_word)
                            
                            if clean_word_match and len(potential_word) > 1:
                                word = potential_word.strip()
                                
                                # 提取年份 (尋找 05-14)
                                full_row_text = " ".join(row)
                                years_found = re.findall(r'\b(0[5-9]|1[0-4])\b', full_row_text)
                                years_list = [int(y) + 100 for y in years_found]
                                years_list = sorted(list(set(years_list)))
                                
                                data.append({
                                    "Word": word,
                                    "Definition": definition,
                                    "Frequency": current_freq, # 如果沒抓到 header，預設為 0
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
