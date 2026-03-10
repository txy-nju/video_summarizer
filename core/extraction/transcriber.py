
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential
import openai

class AudioTranscriber:
    def __init__(self, api_key: str, base_url: str = None):
        """
        初始化 AudioTranscriber。

        Args:
            api_key (str): OpenAI API Key。
            base_url (str, optional): OpenAI API 的中转地址。默认为 None。
        """
        self.api_key = api_key
        self.base_url = base_url
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def transcribe(self, audio_path: Path) -> str:
        """
        调用 Whisper API 将音频转录为 VTT 格式的文本。

        Args:
            audio_path (Path): 音频文件的路径。

        Returns:
            str: VTT 格式的转录文本，包含时间戳。
        """
        print(f"Transcribing audio file: {audio_path}...")
        with open(audio_path, "rb") as audio_file:
            transcript = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="vtt"  # 请求 VTT 格式以获取时间戳
            )
        
        print("Transcription successful.")
        # The transcript object is a string when response_format is 'vtt'
        return transcript
