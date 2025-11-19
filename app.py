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
            tts.write_to_fp(mp3_fp)
            mp3_fp.seek(0)
            word_sound = AudioSegment.from_file(mp3_fp, format="mp3")
            combined += word_sound + silence
        except Exception as e:
            print(f"Error: {e}")
        
        my_bar.progress((i + 1) / total, text=f"正在合成: {word} ({i+1}/{total})")
            
    my_bar.empty()
    return combined

# --- 主程式介面 ---

st.title("🎧 會考英文單字聽力生成器 v3.0 (強力解析版)")

# 1. 檔案讀取
default_pdf = "vocabulary.pdf"
uploaded_file = st.file_uploader("上傳 PDF (或直接使用預設檔案)", type="pdf")

target_file = None
if uploaded_file is not None:
    with open("temp_uploaded.pdf", "wb") as f:
        f.write(uploaded_file.getbuffer())
    target_file = "temp_uploaded.pdf"
elif os.path.exists(default_pdf):
    target_file = default_pdf

# 狀態容器
status_container = st.container()

if target_file:
    # 開始解析
    df, logs = parse_pdf(target_file)
    
    # 如果解析失敗或沒有資料
    if df.empty:
        status_container.error("⚠️ 檔案已讀取，但未解析到任何單字。")
        with st.expander("查看詳細除錯紀錄 (Debug Log)"):
            for log in logs:
                st.write(log)
        st.info("請確認您的 PDF 是否為純圖片檔？如果是圖片檔，本工具無法讀取。")
        
    else:
        status_container.success(f"✅ 成功載入！共發現 {len(df)} 個單字。")
        
        # --- 2. 側邊欄篩選設定 ---
        st.sidebar.header("🛠️ 播放清單設定")
        st.sidebar.text(f"總單字量: {len(df)}")
        
        # 篩選模式
        filter_mode = st.sidebar.radio("選擇篩選模式", 
                                       ["隨機挑選 (Random)", "依序挑選 (Sequential)", "自訂篩選 (Advanced)"])
        
        filtered_df = df.copy()
        
        if filter_mode == "依序挑選 (Sequential)":
            page_size = 20
            max_page = (len(df) // page_size) + 1
            page_num = st.sidebar.number_input(f"選擇頁數 (每頁20字, 共{max_page}頁)", min_value=1, max_value=max_page, value=1)
            start_idx = (page_num - 1) * page_size
            filtered_df = df.iloc[start_idx : start_idx + page_size]
            
        elif filter_mode == "自訂篩選 (Advanced)":
            # 頻率篩選 (檢查是否有 Frequency 資料)
            if df['Frequency'].sum() > 0:
                freq_options = st.sidebar.multiselect(
                    "頻率等級 (Stars)",
                    ["高頻 (8-10次)", "中頻 (4-7次)", "低頻 (1-3次)"],
                    default=["高頻 (8-10次)", "中頻 (4-7次)", "低頻 (1-3次)"]
                )
                freq_filter = []
                if "高頻 (8-10次)" in freq_options: freq_filter.extend([8, 9, 10])
                if "中頻 (4-7次)" in freq_options: freq_filter.extend([4, 5, 6, 7])
                if "低頻 (1-3次)" in freq_options: freq_filter.extend([1, 2, 3])
                if freq_filter:
                    filtered_df = filtered_df[filtered_df['Frequency'].isin(freq_filter)]
            
            # 字母篩選
            letters = sorted(list(set([w[0].upper() for w in df['Word'] if w])))
            selected_letter = st.sidebar.selectbox("開頭字母", ["All"] + letters)
            if selected_letter != "All":
                filtered_df = filtered_df[filtered_df['Word'].str.startswith(selected_letter, na=False)]

            # 年份篩選
            year_input = st.sidebar.selectbox("出現年份 (選填)", ["All"] + list(range(105, 115)))
            if year_input != "All":
                filtered_df = filtered_df[filtered_df['Years'].apply(lambda x: year_input in x)]
            
            # 隨機取20個
            if len(filtered_df) > 20:
                filtered_df = filtered_df.sample(n=20)
                
        else:
            # Random
            if len(filtered_df) > 20:
                filtered_df = filtered_df.sample(n=20)

        # 間隔設定
        silence_sec = st.sidebar.selectbox("單字間隔時間 (秒)", [5, 10, 15])

        # --- 3. 主畫面顯示 ---
        st.subheader(f"📝 練習清單 ({len(filtered_df)} words)")
        
        st.dataframe(
            filtered_df[['Word', 'Definition', 'Frequency', 'Year_Str']],
            column_config={
                "Word": "單字",
                "Definition": "中文解釋",
                "Frequency": st.column_config.NumberColumn("出現次數", format="%d ⭐"),
                "Year_Str": "年份"
            },
            use_container_width=True,
            hide_index=True
        )
        
        # --- 4. 生成音訊 ---
        st.divider()
        if st.button("▶️ 生成語音播放清單", type="primary"):
            if filtered_df.empty:
                st.error("清單為空，請調整篩選條件。")
            else:
                audio_segment = combine_audio(filtered_df, silence_sec)
                buffer = io.BytesIO()
                audio_segment.export(buffer, format="mp3")
                buffer.seek(0)
                
                st.success("生成完畢！")
                st.audio(buffer, format='audio/mp3')
                st.download_button("📥 下載 MP3", data=buffer, file_name="vocab_playlist.mp3", mime="audio/mp3")

else:
    st.info("請上傳 PDF 檔案以開始使用。")
