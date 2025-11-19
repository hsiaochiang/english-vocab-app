import streamlit as st
import pdfplumber
import pandas as pd
import re
from gtts import gTTS
from pydub import AudioSegment
import io
import os

# 設定頁面配置
st.set_page_config(page_title="學測英文單字聽力生成器 v7.0 (錨點搜尋版)", layout="wide")

def clean_word_candidate(text):
    """
    清理候選文字，移除年份數字、中文、標點，只留下最像單字的英文。
    """
    if not text: return None
    # 移除年份數字 (例如 05 06 10)
    text_no_digits = re.sub(r'\d+', '', text)
    # 移除中文
    text_no_chinese = re.sub(r'[\u4e00-\u9fff]', '', text_no_digits)
    # 移除常見雜訊
    clean_text = text_no_chinese.replace("Level", "").replace("Page", "").strip()
    
    # 尋找最長的連續英文字串 (支援連字號 - 和單引號 ')
    # 例如 "05 06 access" -> 抓出 "access"
    match = re.search(r"([a-zA-Z\-\'’\s]+)", clean_text)
    if match:
        word = match.group(1).strip()
        if len(word) > 1: # 排除單一字母雜訊
            return word
    return None

# --- 核心功能 1: 解析 PDF (v7 錨點搜尋版) ---
@st.cache_data
def parse_pdf(pdf_path):
    data = []
    debug_logs = [] 
    
    if not os.path.exists(pdf_path):
        return pd.DataFrame(), ["錯誤：找不到 PDF 檔案"]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            debug_logs.append(f"PDF 共有 {total_pages} 頁")

            for p_idx, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text: continue
                
                lines = text.split('\n')
                
                # 1. 抓取頻率
                current_freq = 0
                freq_match = re.search(r'出現次數\s*[:：]\s*(\d+)', text)
                if freq_match:
                    current_freq = int(freq_match.group(1))
                
                # 定義詞性錨點 (這是我們最可靠的特徵)
                # 包含常見縮寫: v. n. adj. adv. prep. conj. pron. aux. art. num. int.
                anchor_pattern = r'(\[\s*(v\.|n\.|adj\.|adv\.|prep\.|conj\.|pron\.|aux\.|art\.|num\.|int\.|pl\.|缩写|縮寫).*)'
                
                for i, line in enumerate(lines):
                    # 搜尋這一行是否有詞性標記
                    match = re.search(anchor_pattern, line, re.IGNORECASE)
                    
                    if match:
                        # 找到錨點了！
                        definition_part = match.group(1) # 抓出 "[n.] 之後的所有文字"
                        
                        word = None
                        
                        # 策略 A: 單字在同一行，位於詞性標記的左邊
                        # 例如: "access [n.] 通道"
                        prefix_text = line[:match.start()]
                        word = clean_word_candidate(prefix_text)
                        
                        # 策略 B: 單字在上一行
                        # 例如: 
                        # "access"
                        # "[n.] 通道"
                        # 或者 "05 06 access" (有年份雜訊)
                        if not word and i > 0:
                            prev_line = lines[i-1]
                            word = clean_word_candidate(prev_line)
                            
                        # 策略 C: 極端情況，單字在上上一行 (中間夾了年份行)
                        if not word and i > 1:
                            prev_prev_line = lines[i-2]
                            # 確保上一行看起來像是年份或無意義的雜訊
                            if re.search(r'\d+', lines[i-1]) or not lines[i-1].strip():
                                word = clean_word_candidate(prev_prev_line)

                        if word and definition_part:
                            # 提取年份 (尋找附近行的 05-14)
                            # 我們搜尋當前行 + 上一行 + 下一行
                            context_text = line
                            if i > 0: context_text += " " + lines[i-1]
                            if i < len(lines) - 1: context_text += " " + lines[i+1]
                            
                            years_found = re.findall(r'\b(0[5-9]|1[0-4])\b', context_text)
                            years_list = [int(y) + 100 for y in years_found]
                            years_list = sorted(list(set(years_list)))
                            
                            data.append({
                                "Word": word,
                                "Definition": definition_part,
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
            print(f"Error for {word}: {e}")
        
        my_bar.progress((i + 1) / total, text=f"正在合成: {word} ({i+1}/{total})")
            
    my_bar.empty()
    return combined

# --- 主程式介面 ---

st.title("🎧 學測英文單字聽力生成器 v7.0 (最終版)")

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
            # 頻率篩選
            if df['Frequency'].sum() > 0:
                freq_options = st.sidebar.multiselect(
                    "頻率等級 (出現次數)",
                    ["高頻 (8-10次)", "中頻 (4-7次)", "低頻 (1-3次)"],
                    default=["高頻 (8-10次)", "中頻 (4-7次)"]
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
            all_years = sorted(list(set([y for sublist in df['Years'] for y in sublist])))
            year_input = st.sidebar.selectbox("出現年份 (民國)", ["All"] + all_years)
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
