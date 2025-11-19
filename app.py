import streamlit as st
import pdfplumber
import pandas as pd
import re
from gtts import gTTS
from pydub import AudioSegment
import io
import os

# 設定頁面配置
st.set_page_config(page_title="學測英文單字聽力生成器 v9.0", layout="wide")

# --- 核心功能 1: 解析 PDF (v9 亂碼倖存版) ---
@st.cache_data
def parse_pdf(pdf_path):
    """
    解析學測單字 PDF。
    v9修正：針對中文解釋變成 '○○○' 的情況，改用純英文特徵抓取。
    """
    data = []
    
    if not os.path.exists(pdf_path):
        return pd.DataFrame()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                
                lines = text.split('\n')
                
                # 1. 抓取頻率 (嘗試抓取，若無則預設)
                current_freq = 0
                freq_match = re.search(r'出現次數.*[:：]\s*(\d+)', text)
                if freq_match:
                    current_freq = int(freq_match.group(1))
                
                for line in lines:
                    line = line.strip()
                    if not line: continue

                    # 過濾掉明顯不是單字的行
                    # 1. 過濾掉年份行 (例如: 05 06 07 08)
                    if re.match(r'^[\d\s~]+$', line): continue
                    # 2. 過濾掉標題行
                    if "Level" in line or "Page" in line or "出現次數" in line or "The following" in line: continue
                    if "學測版" in line or "高頻率單字表" in line or "尊重著作權" in line: continue
                    
                    # 3. 核心判斷：這行是以英文字母開頭嗎？
                    # 許多單字行長這樣: "passage ○○○" 或 "unique"
                    # 我們抓取開頭的英文字
                    word_match = re.match(r'^([a-zA-Z\-\'’]+)', line)
                    
                    if word_match:
                        word = word_match.group(1).strip()
                        
                        # 二次確認：單字長度要大於 1 (避免抓到雜訊)
                        if len(word) > 1:
                            # 嘗試抓取年份 (從同一行找)
                            years_found = re.findall(r'\b(0[5-9]|1[0-4])\b', line)
                            years_list = [int(y) + 100 for y in years_found]
                            years_list = sorted(list(set(years_list)))
                            
                            # 因為中文變成了 ○○○，我們給一個預設解釋
                            definition = "詳見 PDF (文字編碼限制)"
                            
                            data.append({
                                "Word": word,
                                "Definition": definition,
                                "Frequency": current_freq,
                                "Years": years_list,
                                "Year_Str": ", ".join(map(str, years_list)) if years_list else "-"
                            })
            
    except Exception as e:
        # 出錯時回傳空，讓主程式處理
        print(f"Error: {e}")
        return pd.DataFrame()

    # 去除重複單字 (保留第一次出現的)
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.drop_duplicates(subset=['Word'], keep='first')
        
    return df

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
            # 生成英文發音
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

st.title("🎧 學測英文單字聽力生成器 v9.0")
st.markdown("⚠️ **注意**：由於 PDF 文字編碼特殊，中文解釋可能無法顯示，但**英文朗讀功能完全正常**。")

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
    df = parse_pdf(target_file)
    
    # 如果解析失敗或沒有資料
    if df.empty:
        status_container.error("⚠️ 檔案已讀取，但未解析到任何單字。")
        st.info("這可能是因為 PDF 格式過於特殊。")
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
            if max_page < 1: max_page = 1
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
            # 這裡需要處理 flatten
            all_years = []
            for sublist in df['Years']:
                all_years.extend(sublist)
            all_years = sorted(list(set(all_years)))
            
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
