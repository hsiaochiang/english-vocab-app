import streamlit as st
import pdfplumber
import pandas as pd
import re
import random
from gtts import gTTS
from pydub import AudioSegment
import io
import os

# 設定頁面配置
st.set_page_config(page_title="會考英文單字聽力練習", layout="wide")

# --- 核心功能 1: 解析 PDF ---
@st.cache_data
def parse_pdf(pdf_path):
    """
    解析會考單字 PDF，提取單字、定義、頻率與年份。
    """
    data = []
    current_freq = 0
    
    # 如果找不到檔案，回傳空值並提示
    if not os.path.exists(pdf_path):
        return pd.DataFrame()

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            
            # 1. 嘗試抓取頁面標題中的「出現次數」
            # 格式通常為 "出現次數:10" 或 "出現次數: 9"
            freq_match = re.search(r'出現次數\s*[:：]\s*(\d+)', text)
            if freq_match:
                current_freq = int(freq_match.group(1))
            
            # 2. 提取表格資料
            # PDFPlumber 的 table extraction 對於这种格式通常能抓出 List of Lists
            tables = page.extract_tables()
            
            for table in tables:
                for row in table:
                    # 清理 row 中的 None
                    row = [cell if cell is not None else "" for cell in row]
                    
                    # 簡單的啟發式演算法來辨識欄位
                    # 我們尋找包含 [v.] [n.] [adj.] 等詞性標記的欄位當作「定義」
                    # 定義欄位的前一欄通常是「單字」
                    
                    word = ""
                    definition = ""
                    years_str = ""
                    
                    # 尋找定義欄位的索引
                    def_index = -1
                    for i, cell in enumerate(row):
                        # 檢查是否包含常見詞性標記
                        if re.search(r'\[(v\.|n\.|adj\.|adv\.|prep\.|conj\.|pron\.|aux\.|art\.|num\.)\]', str(cell)):
                            def_index = i
                            definition = cell.replace('\n', ' ') # 清理換行
                            break
                    
                    if def_index > 0:
                        # 假設單字在定義的前一欄
                        potential_word = row[def_index - 1]
                        # 清理單字 (移除換行、非英文字元)
                        word = re.sub(r'[^a-zA-Z\-\s]', '', str(potential_word)).strip()
                        
                        # 提取年份 (年份可能分散在其他欄位)
                        # 尋找所有符合 05-14 的數字
                        full_row_text = " ".join([str(x) for x in row])
                        years_found = re.findall(r'\b(0[5-9]|1[0-4])\b', full_row_text)
                        # 轉換為民國年 105-114
                        years_list = [int(y) + 100 for y in years_found]
                        years_list = sorted(list(set(years_list))) # 去重並排序
                        
                        if word and definition:
                            data.append({
                                "Word": word,
                                "Definition": definition,
                                "Frequency": current_freq,
                                "Years": years_list,
                                "Year_Str": ", ".join(map(str, years_list)) # 顯示用字串
                            })

    return pd.DataFrame(data)

# --- 核心功能 2: 合併音訊 ---
def combine_audio(playlist_df, silence_duration):
    """
    生成單字音訊並插入靜音片段。
    """
    combined = AudioSegment.empty()
    # 建立靜音片段 (毫秒)
    silence = AudioSegment.silent(duration=silence_duration * 1000)
    
    progress_bar = st.progress(0)
    total = len(playlist_df)
    
    for i, row in playlist_df.iterrows():
        word = row['Word']
        try:
            # 1. 使用 gTTS 生成單字發音
            tts = gTTS(text=word, lang='en')
            mp3_fp = io.BytesIO()
            tts.write_to_fp(mp3_fp)
            mp3_fp.seek(0)
            
            # 2. 讀取為 AudioSegment
            word_sound = AudioSegment.from_file(mp3_fp, format="mp3")
            
            # 3. 合併: 單字 + 靜音
            combined += word_sound + silence
            
        except Exception as e:
            st.error(f"Error generating audio for {word}: {e}")
        
        # 更新進度條
        progress_bar.progress((i + 1) / total)
            
    return combined

# --- 主程式介面 ---

st.title("🎧 會考英文單字聽力生成器")
st.markdown("上傳您的 PDF，客製化生成單字播放清單。")

# 1. 檔案讀取
# 預設讀取 GitHub 上的 vocabulary.pdf，但也允許使用者上傳
default_pdf = "vocabulary.pdf"
uploaded_file = st.file_uploader("上傳 PDF (或直接使用預設檔案)", type="pdf")

if uploaded_file is not None:
    # 暫存上傳的檔案
    with open("temp_uploaded.pdf", "wb") as f:
        f.write(uploaded_file.getbuffer())
    df = parse_pdf("temp_uploaded.pdf")
elif os.path.exists(default_pdf):
    df = parse_pdf(default_pdf)
else:
    st.warning("找不到預設的 vocabulary.pdf，請上傳檔案。")
    df = pd.DataFrame()

if not df.empty:
    # --- 2. 側邊欄篩選設定 ---
    st.sidebar.header("🛠️ 播放清單設定")
    
    # 顯示資料概況
    st.sidebar.text(f"總單字量: {len(df)}")
    
    # A. 篩選條件
    filter_mode = st.sidebar.radio("選擇篩選模式", 
                                   ["隨機挑選 (Random)", "依序挑選 (Sequential)", "自訂篩選 (Advanced)"])
    
    filtered_df = df.copy()
    
    if filter_mode == "依序挑選 (Sequential)":
        # 依序模式
        page_size = 20
        max_page = (len(df) // page_size) + 1
        page_num = st.sidebar.number_input(f"選擇頁數 (每頁20字, 共{max_page}頁)", min_value=1, max_value=max_page, value=1)
        start_idx = (page_num - 1) * page_size
        filtered_df = df.iloc[start_idx : start_idx + page_size]
        
    elif filter_mode == "自訂篩選 (Advanced)":
        # 頻率篩選
        freq_options = st.sidebar.multiselect(
            "頻率等級 (Stars)",
            options=["高頻 (8-10次)", "中頻 (4-7次)", "低頻 (1-3次)"],
            default=["高頻 (8-10次)", "中頻 (4-7次)", "低頻 (1-3次)"]
        )
        
        # 處理頻率邏輯
        freq_filter = []
        if "高頻 (8-10次)" in freq_options: freq_filter.extend([8, 9, 10])
        if "中頻 (4-7次)" in freq_options: freq_filter.extend([4, 5, 6, 7])
        if "低頻 (1-3次)" in freq_options: freq_filter.extend([1, 2, 3])
        
        if freq_filter:
            filtered_df = filtered_df[filtered_df['Frequency'].isin(freq_filter)]
            
        # 字母篩選
        letters = sorted(list(set([w[0].upper() for w in df['Word'] if w])))
        selected_letter = st.sidebar.selectbox("開頭字母 (選填)", ["All"] + letters)
        if selected_letter != "All":
            filtered_df = filtered_df[filtered_df['Word'].str.startswith(selected_letter, na=False)]

        # 年份篩選
        year_input = st.sidebar.selectbox("出現年份 (選填)", ["All"] + list(range(105, 115)))
        if year_input != "All":
            # 篩選該年份有出現在 Years 列表中的單字
            filtered_df = filtered_df[filtered_df['Years'].apply(lambda x: year_input in x)]
            
        # 最後隨機取 20 個 (如果超過)
        if len(filtered_df) > 20:
            filtered_df = filtered_df.sample(n=20)
            
    else:
        # 純隨機模式
        if len(filtered_df) > 20:
            filtered_df = filtered_df.sample(n=20)

    # B. 間隔設定
    silence_sec = st.sidebar.selectbox("單字間隔時間 (秒)", [5, 10, 15])

    # --- 3. 主畫面顯示 ---
    st.subheader(f"📝 本次練習單字 ({len(filtered_df)} words)")
    
    # 顯示漂亮的表格
    st.dataframe(
        filtered_df[['Word', 'Definition', 'Frequency', 'Year_Str']],
        column_config={
            "Word": "單字",
            "Definition": "中文解釋",
            "Frequency": st.column_config.NumberColumn("出現次數", format="%d ⭐"),
            "Year_Str": "出現年份 (民國)"
        },
        use_container_width=True,
        hide_index=True
    )
    
    # --- 4. 生成音訊 ---
    st.divider()
    col1, col2 = st.columns([1, 3])
    
    with col1:
        generate_btn = st.button("▶️ 生成語音播放清單", type="primary")
    
    if generate_btn:
        if filtered_df.empty:
            st.error("沒有選到任何單字，請調整篩選條件。")
        else:
            with st.spinner('正在合成語音...請稍候 (gTTS 需要一點時間)'):
                # 合成音訊
                audio_segment = combine_audio(filtered_df, silence_sec)
                
                # 匯出為 Bytes
                buffer = io.BytesIO()
                audio_segment.export(buffer, format="mp3")
                buffer.seek(0)
                
                st.success("生成完畢！")
                
                # 播放器
                st.audio(buffer, format='audio/mp3')
                
                # 下載按鈕
                st.download_button(
                    label="📥 下載 MP3",
                    data=buffer,
                    file_name="vocab_playlist.mp3",
                    mime="audio/mp3"
                )

else:
    st.info("請上傳 PDF 檔案以開始使用。")