"""
가사 영상 창 - cv2.VideoCapture로 mp4 재생, 오디오 엔진과 동기화
VideoPanel: ttk.Frame 기반 (메인 창에 내장)
VideoWindow (레거시): Toplevel 창 (호환성 유지)
"""

import tkinter as tk
from tkinter import ttk
import cv2
from PIL import Image, ImageTk
import threading
import os
from pathlib import Path
from typing import Optional


class VideoPanel(tk.Frame):
    """
    가사 영상 재생 패널 (tk.Frame)
    - mp4 파일을 cv2.VideoCapture로 읽음
    - 오디오 엔진의 재생 위치에 맞춰 동기화
    - 메인 창의 frame에 embed
    - 영상 없을 때는 안내 텍스트 표시
    """

    def __init__(self, parent, audio_engine, video_file_path: str = None):
        """
        Args:
            parent: 부모 widget (tk.Frame 등)
            audio_engine: AudioEngine 인스턴스
            video_file_path: 재생할 mp4 파일 경로 (None이면 안내 텍스트만 표시)
        """
        super().__init__(parent, bg="black")
        self.engine = audio_engine
        self.video_path = video_file_path

        # 동영상 정보
        self.cap = None
        self.fps = 30
        self.frame_interval_ms = int(1000 / self.fps)  # ~33ms
        self.total_frames = 0
        self.current_frame_index = 0

        # 상태
        self.is_playing = False
        self.update_loop_id = None

        # 프레임 표시 Label
        self.label_video = tk.Label(self, bg="black")
        self.label_video.pack(fill=tk.BOTH, expand=True)

        # 안내 텍스트 (영상 없을 때)
        self.label_no_video = tk.Label(
            self,
            text="받은 곡은 가사 영상 지원됩니다\n(같은 이름의 .mp4 파일이 필요합니다)",
            bg="black",
            fg="gray",
            font=("Arial", 10)
        )

        # 동영상 열기 시도
        if video_file_path and self._open_video():
            self.is_playing = True
            self._update_loop()
        else:
            # 영상 없음 → 안내 텍스트 표시
            self.label_no_video.pack(fill=tk.BOTH, expand=True)
            self.is_playing = False

    def _open_video(self) -> bool:
        """동영상 파일 열기"""
        try:
            if not self.video_path or not os.path.exists(self.video_path):
                return False

            self.cap = cv2.VideoCapture(self.video_path)
            if not self.cap.isOpened():
                return False

            self.fps = self.cap.get(cv2.CAP_PROP_FPS)
            if self.fps <= 0:
                self.fps = 30
            self.frame_interval_ms = int(1000 / self.fps)

            self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.current_frame_index = 0

            return True
        except Exception as e:
            print(f"동영상 열기 실패: {e}")
            return False

    def set_video_file(self, video_file_path: str):
        """동영상 파일 변경"""
        # 기존 영상 정리
        self.stop()
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        self.video_path = video_file_path

        # 새 영상 오픈
        if video_file_path and self._open_video():
            self.label_no_video.pack_forget()
            self.label_video.pack(fill=tk.BOTH, expand=True)
            self.is_playing = True
            self._update_loop()
        else:
            self.label_video.pack_forget()
            self.label_no_video.pack(fill=tk.BOTH, expand=True)
            self.is_playing = False

    def _update_loop(self):
        """프레임 업데이트 루프 (~33ms)"""
        if not self.is_playing or self.cap is None:
            return

        try:
            # 오디오 엔진의 현재 위치(초) 가져오기
            # 템포 조정을 고려한 '원본 영상 기준' 시간
            current_sec = self.engine.get_original_position_seconds()

            # 영상의 현재 위치 (밀리초 → 초)
            video_pos_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

            # 0.3초 이상 어긋나면 seek
            if abs(current_sec - video_pos_ms) > 0.3:
                frame_idx = int(current_sec * self.fps)
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            else:
                # 차이가 작으면 순차 읽기 또는 홀드
                target_frame = int(current_sec * self.fps)
                while self.current_frame_index < target_frame and self.is_playing:
                    ret, _ = self.cap.read()
                    if not ret:
                        self.is_playing = False
                        break
                    self.current_frame_index += 1

            # 프레임 읽기 및 표시
            if self.is_playing:
                ret, frame = self.cap.read()
                if ret:
                    self._display_frame(frame)
                    self.current_frame_index = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
                else:
                    # 동영상 끝에 도달
                    self.is_playing = False
                    return

        except Exception as e:
            print(f"프레임 업데이트 오류: {e}")
            self.is_playing = False
            return

        # 다음 업데이트 스케줄 (widget의 root를 찾아 after 사용)
        root = self.winfo_toplevel()
        self.update_loop_id = root.after(self.frame_interval_ms, self._update_loop)

    def _display_frame(self, frame):
        """cv2 프레임을 tkinter Label에 표시"""
        try:
            # BGR → RGB 변환
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # 종횡비 유지하며 widget 크기에 맞게 리사이즈
            widget_width = self.label_video.winfo_width()
            widget_height = self.label_video.winfo_height()

            if widget_width > 1 and widget_height > 1:
                # 종횡비 계산
                h, w = frame_rgb.shape[:2]
                aspect_ratio = w / h
                widget_aspect = widget_width / widget_height

                if widget_aspect > aspect_ratio:
                    # 높이에 맞추기
                    new_height = widget_height
                    new_width = int(new_height * aspect_ratio)
                else:
                    # 너비에 맞추기
                    new_width = widget_width
                    new_height = int(new_width / aspect_ratio)

                frame_rgb = cv2.resize(frame_rgb, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

            # PIL Image 변환
            pil_image = Image.fromarray(frame_rgb)
            photo_image = ImageTk.PhotoImage(pil_image)

            # Label에 표시
            self.label_video.config(image=photo_image)
            self.label_video.image = photo_image  # 참조 유지

        except Exception as e:
            print(f"프레임 표시 오류: {e}")

    def pause(self):
        """일시정지"""
        self.is_playing = False
        if self.update_loop_id:
            root = self.winfo_toplevel()
            root.after_cancel(self.update_loop_id)
            self.update_loop_id = None

    def resume(self):
        """재개"""
        if not self.is_playing and self.cap is not None:
            self.is_playing = True
            self._update_loop()

    def stop(self):
        """정지 및 처음으로"""
        self.is_playing = False
        if self.update_loop_id:
            root = self.winfo_toplevel()
            root.after_cancel(self.update_loop_id)
            self.update_loop_id = None

        # 처음 프레임으로 초기화
        if self.cap is not None:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.current_frame_index = 0
            ret, frame = self.cap.read()
            if ret:
                self._display_frame(frame)

    def cleanup(self):
        """리소스 정리"""
        self.pause()
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class VideoWindow:
    """
    가사 영상 재생 창 (Toplevel) - 레거시 호환성 유지
    VideoPanel을 내장하는 방식으로 동작
    """

    def __init__(self, root: tk.Tk, audio_engine, video_file_path: str):
        """
        Args:
            root: 메인 tkinter root
            audio_engine: AudioEngine 인스턴스
            video_file_path: 재생할 mp4 파일 경로
        """
        self.root = root
        self.engine = audio_engine
        self.video_path = video_file_path

        # Toplevel 창 생성
        self.window = tk.Toplevel(root)
        self.window.title("가사 영상")
        self.window.geometry("640x480")
        self.window.minsize(320, 240)
        self.window.configure(bg="black")

        # VideoPanel을 Toplevel에 embed
        self.panel = VideoPanel(self.window, audio_engine, video_file_path)
        self.panel.pack(fill=tk.BOTH, expand=True)

        # 전체화면 버튼
        btn_fullscreen = ttk.Button(
            self.window,
            text="[전체화면]",
            command=self._on_fullscreen
        )
        btn_fullscreen.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        # 종료 시 콜백
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_fullscreen(self):
        """전체화면 모드"""
        # 간단한 전체화면: 새 Toplevel + 전체화면 상태
        fs_window = tk.Toplevel(self.root)
        fs_window.attributes("-fullscreen", True)
        fs_window.configure(bg="black")

        # 같은 VideoPanel 로직 (영상만 표시)
        fs_panel = VideoPanel(fs_window, self.engine, self.video_path)
        fs_panel.pack(fill=tk.BOTH, expand=True)

        # ESC로 빠져나가기
        def on_escape(event):
            fs_window.destroy()

        fs_window.bind("<Escape>", on_escape)

    def pause(self):
        """일시정지"""
        self.panel.pause()

    def resume(self):
        """재개"""
        self.panel.resume()

    def stop(self):
        """정지"""
        self.panel.stop()

    def _on_close(self):
        """창 종료"""
        self.panel.cleanup()
        self.window.destroy()

    def is_window_open(self) -> bool:
        """창이 열려있는지 확인"""
        try:
            self.window.winfo_exists()
            return True
        except:
            return False


def get_video_path_for_audio(audio_file_path: str) -> Optional[str]:
    """
    오디오 파일과 같은 이름의 mp4 파일 찾기
    Args:
        audio_file_path: 오디오 파일(mp3 등) 경로
    Returns:
        같은 이름의 mp4 파일 경로, 없으면 None
    """
    try:
        audio_path = Path(audio_file_path)
        video_path = audio_path.parent / f"{audio_path.stem}.mp4"
        if video_path.exists():
            return str(video_path)
    except:
        pass
    return None
