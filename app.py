"""
디스코드 노래방 - tkinter GUI (UI 전면 개편)
새 레이아웃: 상단바 + 좌측 가사 영상(60%) + 우측 제어판(40%) + 하단 마이크 바
마이크 입력, MR 선택, 재생, 이펙트 조절
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
import numpy as np
from datetime import datetime
from audio_engine import AudioEngine
from video_window import VideoPanel, VideoWindow, get_video_path_for_audio


class DiscordKaraoke:
    def __init__(self, root):
        self.root = root
        self.root.title("디스코드 노래방")
        self.root.geometry("1280x800")
        self.root.minsize(1100, 680)

        # UI 초기화 완료 플래그 (콜백 가드용)
        self._ui_ready = False

        # 오디오 엔진
        self.engine = AudioEngine()

        # 앱 기준 폴더: 포터블(frozen)이면 exe 옆, 아니면 소스 폴더
        if getattr(sys, 'frozen', False):
            app_base_dir = Path(sys.executable).parent
        else:
            app_base_dir = Path(__file__).parent

        # 녹음 관련
        self.recording_folder = app_base_dir / "녹음"
        self.last_recording_filepath = None
        self.last_recording_duration = 0.0

        # 설정 파일 경로
        self.settings_file = app_base_dir / "settings.json"
        self.settings = self._load_settings()

        # MR 폴더 및 파일 목록
        self.mr_folder = self.settings.get("mr_folder", "")
        self.mr_files = []
        self.current_mr_index = -1

        # 가사 영상 패널
        self.video_panel = None
        self.video_window = None

        # GUI 상태
        self.device_output_var = tk.StringVar()
        self.device_input_var = tk.StringVar()
        self.device_monitor_var = tk.StringVar()
        self.mr_search_var = tk.StringVar()
        self.mr_search_var.trace("w", self._on_mr_search)

        # 장치 목록 캐시 (다이얼로그가 없을 때도 사용)
        self.device_input_list = []
        self.device_output_list = []
        self.device_monitor_list = []
        self.device_latency_options = ["저지연", "균형 (기본)", "안정"]

        # 주기적 갱신용
        self.is_running = True
        self.last_stats_display_time = 0

        # 마이크 무음 감지 관련
        self.silent_mic_count = 0
        self.silent_mic_warning_shown = False
        self.auto_detect_mic_in_progress = False

        # 다크 테마 적용 (sv-ttk 포함)
        self._apply_dark_theme()

        # UI 빌드
        self._build_ui()
        self._ui_ready = True

        # 장치 목록 갱신
        self._refresh_device_lists()

        # 설정 복원
        self._restore_settings()

        # 초기 스트림 시작
        self._start_stream()

        # 주기적 갱신 루프
        self._update_loop()

        # 종료 처리
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _apply_dark_theme(self):
        """다크 테마 적용"""
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
        except ImportError:
            # sv-ttk 미설치 → clam 테마로 폴백
            style = ttk.Style()
            style.theme_use('clam')
            # 어두운 색상 직접 설정
            style.configure("TLabel", foreground="white", background="#2b2b2b")
            style.configure("TButton", foreground="white", background="#3c3c3c")
            style.configure("TFrame", background="#2b2b2b")
            style.configure("TLabelFrame", foreground="white", background="#2b2b2b")

    def _build_ui(self):
        """UI 빌드 - 새 레이아웃"""
        # 전체 컨테이너
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)
        main_container.columnconfigure(0, weight=1)
        main_container.rowconfigure(1, weight=1)

        # ===== 상단바 (설정 + 상태) =====
        self._build_topbar(main_container)

        # ===== 중앙 (좌측 영상 60% + 우측 제어판 40%) =====
        central = ttk.Frame(main_container)
        central.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        central.columnconfigure(0, weight=6)  # 좌측 60%
        central.columnconfigure(1, weight=4)  # 우측 40%
        central.rowconfigure(0, weight=1)

        # 좌측: 가사 영상 패널 + 곡명 + 진행바 + 컨트롤
        self._build_left_panel(central)

        # 우측: 검색 + 곡 리스트 + 유튜브 + 폴더
        self._build_right_panel(central)

        # ===== 하단 마이크 바 =====
        self._build_bottom_mic_bar(main_container)

    def _build_topbar(self, parent):
        """상단바: 설정 버튼 + 상태 라벨"""
        topbar = ttk.Frame(parent)
        topbar.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        topbar.columnconfigure(1, weight=1)

        # 설정 버튼
        btn_settings = ttk.Button(topbar, text="⚙ 설정", command=self._on_settings_click, width=12)
        btn_settings.grid(row=0, column=0, sticky="w", padx=5)

        # 상태 라벨 (멀티라인 가능)
        self.label_status = tk.Label(
            topbar,
            text="상태: 준비 중...",
            foreground="lightblue",
            background="#2b2b2b",
            font=("Arial", 9),
            justify=tk.LEFT,
            wraplength=800
        )
        self.label_status.grid(row=0, column=1, sticky="ew", padx=10)

    def _build_left_panel(self, parent):
        """좌측 패널: 가사 영상 + 곡명 + 진행바 + 컨트롤"""
        left_container = ttk.Frame(parent)
        left_container.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_container.rowconfigure(0, weight=1)  # 영상 패널
        left_container.columnconfigure(0, weight=1)

        # 가사 영상 패널 (빈 상태로 시작)
        self.video_panel = VideoPanel(left_container, self.engine, None)
        self.video_panel.grid(row=0, column=0, sticky="nsew")

        # 곡명 + 진행바 + 재생 컨트롤 frame
        control_frame = ttk.Frame(left_container)
        control_frame.grid(row=1, column=0, sticky="ew", pady=10)
        control_frame.columnconfigure(0, weight=1)

        # 곡명
        ttk.Label(control_frame, text="현재 곡:", font=("Arial", 9)).grid(row=0, column=0, sticky="w")
        self.label_current = tk.Label(
            control_frame,
            text="(없음)",
            font=("Arial", 11, "bold"),
            foreground="white",
            background="#2b2b2b"
        )
        self.label_current.grid(row=0, column=1, sticky="ew", padx=5)

        # 진행바
        ttk.Label(control_frame, text="진행:", font=("Arial", 9)).grid(row=1, column=0, sticky="w", pady=5)
        slider_frame = ttk.Frame(control_frame)
        slider_frame.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        slider_frame.columnconfigure(0, weight=1)

        self.slider_progress = ttk.Scale(slider_frame, from_=0, to=100, orient=tk.HORIZONTAL, command=self._on_progress_seek)
        self.slider_progress.grid(row=0, column=0, sticky="ew")

        self.label_time = tk.Label(
            slider_frame,
            text="00:00 / 00:00",
            font=("Arial", 8),
            foreground="gray",
            background="#2b2b2b"
        )
        self.label_time.grid(row=0, column=1, sticky="e", padx=5)

        # 재생 버튼 (한 줄: 재생/일시정지/정지 + 전체화면)
        btn_frame = ttk.Frame(control_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=5)
        btn_frame.columnconfigure(3, weight=1)

        self.btn_play = ttk.Button(btn_frame, text="▶", command=self._on_play, width=3)
        self.btn_play.grid(row=0, column=0, padx=2)

        self.btn_pause = ttk.Button(btn_frame, text="⏸", command=self._on_pause, width=3)
        self.btn_pause.grid(row=0, column=1, padx=2)

        self.btn_stop = ttk.Button(btn_frame, text="⏹", command=self._on_stop, width=3)
        self.btn_stop.grid(row=0, column=2, padx=2)

        # 전체화면 버튼 (우측에 배치)
        self.btn_fullscreen = ttk.Button(btn_frame, text="[전체화면]", command=self._on_fullscreen_video)
        self.btn_fullscreen.grid(row=0, column=4, padx=5)

        # 키/템포 컨트롤 (한 줄)
        control2_frame = ttk.Frame(control_frame)
        control2_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=5)
        control2_frame.columnconfigure(5, weight=1)

        ttk.Label(control2_frame, text="키:", font=("Arial", 9)).grid(row=0, column=0, sticky="w")
        self.btn_key_down = ttk.Button(control2_frame, text="[-]", width=3, command=self._on_key_down)
        self.btn_key_down.grid(row=0, column=1, padx=2)
        self.label_key = tk.Label(control2_frame, text="키: 0", font=("Arial", 9), foreground="white", background="#2b2b2b")
        self.label_key.grid(row=0, column=2, padx=2)
        self.btn_key_up = ttk.Button(control2_frame, text="[+]", width=3, command=self._on_key_up)
        self.btn_key_up.grid(row=0, column=3, padx=2)

        ttk.Label(control2_frame, text="템포:", font=("Arial", 9)).grid(row=0, column=6, sticky="w", padx=(10, 0))
        self.btn_tempo_down = ttk.Button(control2_frame, text="[-]", width=3, command=self._on_tempo_down)
        self.btn_tempo_down.grid(row=0, column=7, padx=2)
        self.label_tempo = tk.Label(control2_frame, text="템포: 1.00x", font=("Arial", 9), foreground="white", background="#2b2b2b")
        self.label_tempo.grid(row=0, column=8, padx=2)
        self.btn_tempo_up = ttk.Button(control2_frame, text="[+]", width=3, command=self._on_tempo_up)
        self.btn_tempo_up.grid(row=0, column=9, padx=2)

        self.btn_reset_all = ttk.Button(control2_frame, text="[원래대로]", command=self._on_reset_all)
        self.btn_reset_all.grid(row=0, column=10, padx=5)

        # MR 볼륨 슬라이더
        control3_frame = ttk.Frame(control_frame)
        control3_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=5)
        control3_frame.columnconfigure(1, weight=1)

        ttk.Label(control3_frame, text="MR 볼륨:", font=("Arial", 9)).grid(row=0, column=0, sticky="w")
        self.slider_mr_vol = ttk.Scale(control3_frame, from_=0, to=1.5, orient=tk.HORIZONTAL, command=self._on_mr_volume)
        self.slider_mr_vol.set(1.0)
        self.slider_mr_vol.grid(row=0, column=1, sticky="ew", padx=5)
        self.label_mr_vol = tk.Label(control3_frame, text="1.0", font=("Arial", 9), foreground="white", background="#2b2b2b", width=4)
        self.label_mr_vol.grid(row=0, column=2, sticky="e", padx=5)

        # 녹음 버튼
        record_frame = ttk.Frame(control_frame)
        record_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=5)
        record_frame.columnconfigure(1, weight=1)

        self.btn_recording = ttk.Button(record_frame, text="● 녹음", command=self._on_recording_toggle, width=10)
        self.btn_recording.grid(row=0, column=0, padx=5)

        self.label_recording_status = tk.Label(
            record_frame,
            text="",
            font=("Arial", 8),
            foreground="gray",
            background="#2b2b2b"
        )
        self.label_recording_status.grid(row=0, column=1, sticky="ew", padx=5)

        self.btn_open_recording = ttk.Button(record_frame, text="[녹음 폴더]", command=self._on_open_recording_folder, state=tk.DISABLED, width=12)
        self.btn_open_recording.grid(row=0, column=2, padx=5)

    def _build_right_panel(self, parent):
        """우측 패널: 검색 + 곡 리스트 + 유튜브 + 폴더"""
        right_container = ttk.Frame(parent)
        right_container.grid(row=0, column=1, sticky="nsew")
        right_container.rowconfigure(1, weight=1)
        right_container.columnconfigure(0, weight=1)

        # 검색창
        search_frame = ttk.Frame(right_container)
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        search_frame.columnconfigure(1, weight=1)

        ttk.Label(search_frame, text="검색:", font=("Arial", 9)).grid(row=0, column=0, sticky="w")
        entry_search = ttk.Entry(search_frame, textvariable=self.mr_search_var, font=("Arial", 9))
        entry_search.grid(row=0, column=1, sticky="ew", padx=5)

        # 곡 리스트
        list_frame = ttk.Frame(right_container)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.listbox_mr = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            font=("Arial", 9),
            bg="#3c3c3c",
            fg="white",
            selectmode=tk.SINGLE
        )
        self.listbox_mr.grid(row=0, column=0, sticky="nsew")
        self.listbox_mr.bind("<Double-Button-1>", self._on_mr_double_click)
        scrollbar.config(command=self.listbox_mr.yview)

        # MR 폴더 선택 + 유튜브 다운로드
        folder_frame = ttk.Frame(right_container)
        folder_frame.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        folder_frame.columnconfigure(1, weight=1)

        btn_folder = ttk.Button(folder_frame, text="[MR 폴더]", command=self._on_select_mr_folder, width=12)
        btn_folder.grid(row=0, column=0, padx=(0, 5))

        self.label_folder = tk.Label(
            folder_frame,
            text="폴더: (선택 안 함)",
            font=("Arial", 8),
            foreground="gray",
            background="#2b2b2b"
        )
        self.label_folder.grid(row=0, column=1, sticky="ew")

        # 유튜브 URL + 다운로드
        yt_frame = ttk.Frame(right_container)
        yt_frame.grid(row=3, column=0, sticky="ew", pady=5)
        yt_frame.columnconfigure(1, weight=1)

        ttk.Label(yt_frame, text="URL:", font=("Arial", 9)).grid(row=0, column=0, sticky="w")
        self.entry_yt_url = ttk.Entry(yt_frame, font=("Arial", 9))
        self.entry_yt_url.grid(row=0, column=1, sticky="ew", padx=5)

        self.btn_yt_download = ttk.Button(yt_frame, text="가져오기", command=self._on_youtube_download, width=10)
        self.btn_yt_download.grid(row=0, column=2, padx=(5, 0))

        # 다운로드 상태
        self.label_yt_status = tk.Label(
            right_container,
            text="",
            font=("Arial", 8),
            foreground="blue",
            background="#2b2b2b"
        )
        self.label_yt_status.grid(row=4, column=0, sticky="ew")

    def _build_bottom_mic_bar(self, parent):
        """하단 마이크 바: 레벨미터 + AGC + 에코 + 컨트롤"""
        mic_bar = ttk.Frame(parent)
        mic_bar.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        mic_bar.columnconfigure(0, weight=1)

        # 첫 번째 행: 레벨미터 + AGC 체크박스
        level_frame = ttk.Frame(mic_bar)
        level_frame.grid(row=0, column=0, sticky="ew", pady=3)
        level_frame.columnconfigure(1, weight=1)

        ttk.Label(level_frame, text="레벨:", font=("Arial", 8)).grid(row=0, column=0, sticky="w")
        self.progressbar_mic_level = ttk.Progressbar(level_frame, orient=tk.HORIZONTAL, mode='determinate', length=100)
        self.progressbar_mic_level.grid(row=0, column=1, sticky="ew", padx=5)
        self.label_agc_gain = tk.Label(level_frame, text="x1.0", font=("Arial", 8), foreground="white", background="#2b2b2b", width=6)
        self.label_agc_gain.grid(row=0, column=2, sticky="e", padx=5)

        self.var_agc_enabled = tk.BooleanVar(value=True)
        chk_agc = ttk.Checkbutton(level_frame, text="자동볼륨", variable=self.var_agc_enabled, command=self._on_agc_toggle)
        chk_agc.grid(row=0, column=3, sticky="w", padx=5)

        self.var_voice_correction_enabled = tk.BooleanVar(value=True)
        chk_vc = ttk.Checkbutton(level_frame, text="보정", variable=self.var_voice_correction_enabled, command=self._on_voice_correction_toggle)
        chk_vc.grid(row=0, column=4, sticky="w", padx=5)

        self.combo_voice_strength = ttk.Combobox(
            level_frame,
            values=["약", "중 (기본)", "강"],
            state="readonly",
            width=8,
            font=("Arial", 8)
        )
        self.combo_voice_strength.set("중 (기본)")
        self.combo_voice_strength.bind("<<ComboboxSelected>>", self._on_voice_correction_strength_change)
        self.combo_voice_strength.grid(row=0, column=5, sticky="w", padx=5)

        # 두 번째 행: 에코 + 볼륨 슬라이더들
        control_frame = ttk.Frame(mic_bar)
        control_frame.grid(row=1, column=0, sticky="ew", pady=3)
        control_frame.columnconfigure(4, weight=1)

        ttk.Label(control_frame, text="에코:", font=("Arial", 8)).grid(row=0, column=0, sticky="w")
        self.combo_echo = ttk.Combobox(
            control_frame,
            values=["끄기", "노래방 (기본)", "콘서트홀", "동굴"],
            state="readonly",
            width=12,
            font=("Arial", 8)
        )
        self.combo_echo.set("노래방 (기본)")
        self.combo_echo.bind("<<ComboboxSelected>>", self._on_echo_preset)
        self.combo_echo.grid(row=0, column=1, sticky="w", padx=5)

        # 고급 버튼 (에코 슬라이더들 토글)
        self.btn_advanced = ttk.Button(control_frame, text="[고급▾]", command=self._on_toggle_advanced, width=8)
        self.btn_advanced.grid(row=0, column=2, padx=5)

        # 우측: MR/마이크/모니터 볼륨
        ttk.Label(control_frame, text="MR", font=("Arial", 8)).grid(row=0, column=5, sticky="w", padx=5)
        self.slider_mr_vol_bar = ttk.Scale(control_frame, from_=0, to=1.5, orient=tk.HORIZONTAL, command=self._on_mr_volume)
        self.slider_mr_vol_bar.set(1.0)
        self.slider_mr_vol_bar.grid(row=0, column=6, sticky="ew", padx=2)

        ttk.Label(control_frame, text="마이크", font=("Arial", 8)).grid(row=0, column=7, sticky="w", padx=5)
        self.slider_mic_vol = ttk.Scale(control_frame, from_=0, to=1.5, orient=tk.HORIZONTAL, command=self._on_mic_volume)
        self.slider_mic_vol.set(1.0)
        self.slider_mic_vol.grid(row=0, column=8, sticky="ew", padx=2)

        ttk.Label(control_frame, text="모니터", font=("Arial", 8)).grid(row=0, column=9, sticky="w", padx=5)
        self.slider_monitor_vol = ttk.Scale(control_frame, from_=0, to=1.5, orient=tk.HORIZONTAL, command=self._on_monitor_volume)
        self.slider_monitor_vol.set(1.0)
        self.slider_monitor_vol.grid(row=0, column=10, sticky="ew", padx=2)

        # 고급 슬라이더들 (초기: 숨김)
        self.advanced_frame = ttk.Frame(mic_bar)
        self.advanced_frame.grid(row=2, column=0, sticky="ew", pady=3)
        self.advanced_frame.columnconfigure(4, weight=1)
        self.advanced_frame_visible = False

        labels = ["에코 딜레이", "에코 피드백", "에코 믹스", "리버브"]
        self.sliders_advanced = {}

        for i, label in enumerate(labels):
            ttk.Label(self.advanced_frame, text=label, font=("Arial", 8)).grid(row=0, column=i*2, sticky="w", padx=5)
            slider = ttk.Scale(self.advanced_frame, from_=0, to=1, orient=tk.HORIZONTAL)
            slider.grid(row=0, column=i*2+1, sticky="ew", padx=2)
            self.sliders_advanced[label] = slider

        self.slider_delay = self.sliders_advanced["에코 딜레이"]
        self.slider_delay.config(from_=0.05, to=0.6)
        self.slider_delay.set(0.2)
        self.slider_delay.configure(command=self._on_delay_change)

        self.slider_feedback = self.sliders_advanced["에코 피드백"]
        self.slider_feedback.set(0.4)
        self.slider_feedback.configure(command=self._on_feedback_change)

        self.slider_echo_mix = self.sliders_advanced["에코 믹스"]
        self.slider_echo_mix.set(0.2)
        self.slider_echo_mix.configure(command=self._on_echo_mix_change)

        self.slider_reverb = self.sliders_advanced["리버브"]
        self.slider_reverb.set(0.3)
        self.slider_reverb.configure(command=self._on_reverb_change)

    def _on_toggle_advanced(self):
        """고급 슬라이더들 토글"""
        if self.advanced_frame_visible:
            self.advanced_frame.grid_remove()
            self.btn_advanced.config(text="[고급▾]")
            self.advanced_frame_visible = False
        else:
            self.advanced_frame.grid()
            self.btn_advanced.config(text="[고급▲]")
            self.advanced_frame_visible = True

    def _on_settings_click(self):
        """설정 버튼 클릭 → 설정 다이얼로그"""
        self._open_settings_dialog()

    def _open_settings_dialog(self):
        """설정 다이얼로그 (Toplevel, 모달) - 콤보는 로컬 변수로 관리"""
        dlg = tk.Toplevel(self.root)
        dlg.title("설정")
        dlg.geometry("500x400")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        # 장치 선택 (로컬 콤보, 다이얼로그가 닫혀도 자동 소멸)
        ttk.Label(dlg, text="마이크 입력:", font=("Arial", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=10)
        combo_input = ttk.Combobox(dlg, textvariable=self.device_input_var, state="readonly", width=40)
        combo_input['values'] = self.device_input_list
        combo_input.grid(row=0, column=1, sticky="ew", padx=10, pady=10)

        ttk.Button(dlg, text="[마이크 자동 감지]", command=self._on_auto_detect_mic).grid(row=0, column=2, padx=5)

        ttk.Label(dlg, text="메인 출력 (CABLE):", font=("Arial", 9)).grid(row=1, column=0, sticky="w", padx=10, pady=10)
        combo_output = ttk.Combobox(dlg, textvariable=self.device_output_var, state="readonly", width=40)
        combo_output['values'] = self.device_output_list
        combo_output.grid(row=1, column=1, sticky="ew", padx=10, pady=10)

        ttk.Label(dlg, text="모니터 출력:", font=("Arial", 9)).grid(row=2, column=0, sticky="w", padx=10, pady=10)
        combo_monitor = ttk.Combobox(dlg, textvariable=self.device_monitor_var, state="readonly", width=40)
        combo_monitor['values'] = self.device_monitor_list
        combo_monitor.grid(row=2, column=1, sticky="ew", padx=10, pady=10)

        ttk.Label(dlg, text="지연 설정:", font=("Arial", 9)).grid(row=3, column=0, sticky="w", padx=10, pady=10)
        combo_latency = ttk.Combobox(
            dlg,
            values=self.device_latency_options,
            state="readonly",
            width=20
        )

        # 현재 지연 모드를 콤보에 표시
        if hasattr(self, '_current_latency_mode_text'):
            combo_latency.set(self._current_latency_mode_text)
        else:
            combo_latency.set("균형 (기본)")
            self._current_latency_mode_text = "균형 (기본)"

        # 지연 모드 변경 핸들러 (다이얼로그 닫혀도 상태 유지)
        def on_latency_change(event):
            self._current_latency_mode_text = combo_latency.get()
            self._on_latency_mode_change(event)

        combo_latency.bind("<<ComboboxSelected>>", on_latency_change)
        combo_latency.grid(row=3, column=1, sticky="w", padx=10, pady=10)

        ttk.Label(dlg, text="(끊김 → 안정, 지연 크면 → 저지연)", font=("Arial", 7), foreground="gray").grid(row=4, column=0, columnspan=2, sticky="w", padx=10)

        # 버튼
        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=10, pady=20)
        btn_frame.columnconfigure(1, weight=1)

        ttk.Button(btn_frame, text="적용 / 재시작", command=self._on_apply_devices, width=15).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="닫기", command=dlg.destroy, width=15).grid(row=0, column=2, padx=5)

    def _load_settings(self) -> dict:
        """설정 파일 로드"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            pass
        return {}

    def _save_settings(self):
        """설정 파일 저장"""
        try:
            # 지연 모드는 현재 상태 변수에서 읽음
            mode_text = getattr(self, '_current_latency_mode_text', "균형 (기본)")
            mode_map = {
                "저지연": "low",
                "균형 (기본)": "balanced",
                "안정": "stable"
            }
            latency_mode = mode_map.get(mode_text, "balanced")

            settings = {
                "mr_folder": self.mr_folder,
                "input_device": self.device_input_var.get(),
                "output_device": self.device_output_var.get(),
                "monitor_device": self.device_monitor_var.get(),
                "mr_volume": self.slider_mr_vol.get(),
                "mic_volume": self.slider_mic_vol.get(),
                "monitor_volume": self.slider_monitor_vol.get(),
                "echo_preset": self.combo_echo.get(),
                "delay_seconds": self.slider_delay.get(),
                "feedback": self.slider_feedback.get(),
                "echo_mix": self.slider_echo_mix.get(),
                "reverb": self.slider_reverb.get(),
                "agc_enabled": self.var_agc_enabled.get(),
                "latency_mode": latency_mode,
                "voice_correction_enabled": self.var_voice_correction_enabled.get(),
                "voice_correction_strength": self.combo_voice_strength.get(),
            }
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._show_error(f"설정 저장 실패: {e}")

    def _restore_settings(self):
        """저장된 설정 복원"""
        try:
            if "input_device" in self.settings:
                self.device_input_var.set(self.settings["input_device"])
            if "output_device" in self.settings:
                self.device_output_var.set(self.settings["output_device"])
            if "monitor_device" in self.settings:
                self.device_monitor_var.set(self.settings["monitor_device"])

            self.slider_mr_vol.set(self.settings.get("mr_volume", 1.0))
            self.slider_mic_vol.set(self.settings.get("mic_volume", 1.0))
            self.slider_monitor_vol.set(self.settings.get("monitor_volume", 1.0))
            self.slider_delay.set(self.settings.get("delay_seconds", 0.2))
            self.slider_feedback.set(self.settings.get("feedback", 0.4))
            self.slider_echo_mix.set(self.settings.get("echo_mix", 0.2))
            self.slider_reverb.set(self.settings.get("reverb", 0.3))

            if "echo_preset" in self.settings:
                self.combo_echo.set(self.settings["echo_preset"])

            if "agc_enabled" in self.settings:
                self.var_agc_enabled.set(self.settings["agc_enabled"])
                self.engine.set_agc_enabled(self.settings["agc_enabled"])

            if "latency_mode" in self.settings:
                latency_mode = self.settings["latency_mode"]
                mode_reverse_map = {
                    "low": "저지연",
                    "balanced": "균형 (기본)",
                    "stable": "안정"
                }
                mode_text = mode_reverse_map.get(latency_mode, "균형 (기본)")
                self._current_latency_mode_text = mode_text  # 상태 변수에 저장
                self.engine.set_latency_mode(latency_mode)

            if "voice_correction_enabled" in self.settings:
                enabled = self.settings["voice_correction_enabled"]
                self.var_voice_correction_enabled.set(enabled)

                if "voice_correction_strength" in self.settings:
                    strength_text = self.settings["voice_correction_strength"]
                    self.combo_voice_strength.set(strength_text)
                    strength_map = {
                        "약": "weak",
                        "중 (기본)": "medium",
                        "강": "strong"
                    }
                    strength = strength_map.get(strength_text, "medium")
                else:
                    strength = "medium"

                self.engine.set_voice_correction(enabled, strength)

            if "mr_folder" in self.settings and self.settings["mr_folder"]:
                self.mr_folder = self.settings["mr_folder"]
                self._load_mr_files()
        except Exception as e:
            pass

    def _refresh_device_lists(self):
        """장치 목록 갱신 (캐시에만 저장, 다이얼로그 콤보는 열릴 때 채움)"""
        try:
            input_devs, output_devs = self.engine.list_devices()

            self.device_input_list = [f"{d['name']} ({d['hostapi']})" for d in input_devs]
            self.device_output_list = [f"{d['name']} ({d['hostapi']})" for d in output_devs]
            self.device_monitor_list = ["사용 안 함"] + self.device_output_list

            mic_idx, out_idx, mon_idx = self.engine.get_default_devices()
            if 0 <= mic_idx < len(input_devs):
                self.device_input_var.set(self.device_input_list[input_devs.index(next(d for d in input_devs if d['index'] == mic_idx))])
            if 0 <= out_idx < len(output_devs):
                self.device_output_var.set(self.device_output_list[output_devs.index(next(d for d in output_devs if d['index'] == out_idx))])
            if 0 <= mon_idx < len(output_devs):
                self.device_monitor_var.set(self.device_monitor_list[output_devs.index(next(d for d in output_devs if d['index'] == mon_idx)) + 1])
        except Exception as e:
            print(f"[ERROR] _refresh_device_lists: {e}")

    def _get_selected_device_index(self, device_var, is_input=False) -> int:
        """콤보박스 선택 → 장치 인덱스"""
        try:
            input_devs, output_devs = self.engine.list_devices()
            devs = input_devs if is_input else output_devs

            selected = device_var.get()
            for dev in devs:
                if f"{dev['name']} ({dev['hostapi']})" == selected:
                    return dev['index']
        except:
            pass
        return 0

    def _start_stream(self):
        """스트림 시작"""
        try:
            mic_idx = self._get_selected_device_index(self.device_input_var, is_input=True)
            out_idx = self._get_selected_device_index(self.device_output_var, is_input=False)

            monitor_name = self.device_monitor_var.get()
            monitor_idx = None if monitor_name == "사용 안 함" else self._get_selected_device_index(self.device_monitor_var, is_input=False)

            self.engine.start_stream(mic_idx, out_idx, monitor_idx)
            self._update_status()
        except Exception as e:
            self._show_error(f"스트림 시작 실패: {e}")

    def _update_status(self):
        """상태 표시 갱신"""
        try:
            output_name = self.device_output_var.get().split(" (")[0]
            output_hostapi = self.engine.output_hostapi_name

            estimated_latency_ms = self.engine.get_estimated_latency_ms()

            status = f"상태: 동작 중 · 출력: {output_name} · 경로: {output_hostapi} · 예상 지연: ~{int(estimated_latency_ms)}ms"

            if "CABLE" not in self.device_output_var.get():
                status += " ⚠ VB-Audio Virtual Cable 미발견 (README 참고)"

            try:
                mic_idx = self._get_selected_device_index(self.device_input_var, is_input=True)
                out_idx = self._get_selected_device_index(self.device_output_var, is_input=False)
                mismatched = self.engine.check_device_samplerates(mic_idx, out_idx)

                if mismatched:
                    device_names = ", ".join([f"[{d['device_name']}] {int(d['default_samplerate'])}Hz" for d in mismatched])
                    status += f"\n⚠ {device_names} 기본 형식이 48000Hz가 아닙니다 — Windows 소리 설정에서 48000Hz로 변경 권장"
            except:
                pass

            self.label_status.config(text=status)
        except:
            pass

    def _on_latency_mode_change(self, event=None):
        """지연 모드 변경"""
        if not self._ui_ready:
            return

        # 상태 변수에서 읽음
        mode_text = getattr(self, '_current_latency_mode_text', "균형 (기본)")
        mode_map = {
            "저지연": "low",
            "균형 (기본)": "balanced",
            "안정": "stable"
        }
        mode = mode_map.get(mode_text, "balanced")

        self.engine.set_latency_mode(mode)
        self._save_settings()

        self._start_stream()
        messagebox.showinfo("알림", f"지연 설정이 '{mode_text}'로 변경되었습니다. (스트림 재시작됨)")

    def _on_apply_devices(self):
        """장치 변경 적용"""
        self._save_settings()
        self._start_stream()
        messagebox.showinfo("알림", "장치가 변경되었습니다.")

    def _on_select_mr_folder(self):
        """MR 폴더 선택"""
        folder = filedialog.askdirectory(title="MR 폴더 선택")
        if folder:
            self.mr_folder = folder
            self._load_mr_files()
            self._save_settings()

    def _load_mr_files(self):
        """MR 폴더에서 음악 파일 로드"""
        try:
            self.mr_files = []
            if not self.mr_folder or not Path(self.mr_folder).exists():
                self.label_folder.config(text="폴더: (선택 안 함)", foreground="gray")
                self.listbox_mr.delete(0, tk.END)
                return

            folder_path = Path(self.mr_folder)
            for ext in ['*.mp3', '*.wav', '*.flac', '*.ogg', '*.m4a']:
                self.mr_files.extend(sorted(folder_path.glob(ext)))

            self.label_folder.config(text=f"폴더: {self.mr_folder}", foreground="white")
            self._update_mr_listbox()
        except Exception as e:
            self._show_error(f"MR 파일 로드 실패: {e}")

    def _update_mr_listbox(self):
        """MR 리스트박스 갱신 (검색 필터 포함)"""
        self.listbox_mr.delete(0, tk.END)
        search_text = self.mr_search_var.get().lower()

        for file_path in self.mr_files:
            file_name = file_path.stem
            if search_text in file_name.lower():
                self.listbox_mr.insert(tk.END, file_name)

    def _on_mr_search(self, *args):
        """MR 검색"""
        self._update_mr_listbox()

    def _on_mr_double_click(self, event):
        """MR 더블클릭 → 재생"""
        selection = self.listbox_mr.curselection()
        if not selection:
            return

        visible_index = selection[0]
        search_text = self.mr_search_var.get().lower()

        file_list_index = 0
        file_path_selected = None
        for i, file_path in enumerate(self.mr_files):
            if search_text in file_path.stem.lower():
                if file_list_index == visible_index:
                    file_path_selected = file_path
                    break
                file_list_index += 1

        if not file_path_selected:
            return

        try:
            if self.engine.load_mr(str(file_path_selected)):
                self.engine.reset_pitch_and_tempo()
                self.label_key.config(text="키: 0")
                self.label_tempo.config(text="템포: 1.00x")

                self.label_current.config(text=file_path_selected.stem)

                # 가사 영상 패널 업데이트
                video_path = get_video_path_for_audio(str(file_path_selected))
                if video_path:
                    self.video_panel.set_video_file(video_path)
                else:
                    self.video_panel.set_video_file(None)

                self.engine.play()
                self.current_mr_index = self.mr_files.index(file_path_selected)
        except Exception as e:
            self._show_error(f"곡 로드 실패: {e}")

    def _on_play(self):
        """재생"""
        self.engine.play()
        if self.video_panel:
            self.video_panel.resume()

    def _on_pause(self):
        """일시정지"""
        self.engine.pause()
        if self.video_panel:
            self.video_panel.pause()

    def _on_stop(self):
        """정지"""
        self.engine.stop()
        if self.video_panel:
            self.video_panel.stop()

    def _on_fullscreen_video(self):
        """전체화면 영상 (Toplevel)"""
        if self.current_mr_index >= 0 and self.current_mr_index < len(self.mr_files):
            audio_file = self.mr_files[self.current_mr_index]
            video_path = get_video_path_for_audio(str(audio_file))
            if video_path:
                self.video_window = VideoWindow(self.root, self.engine, video_path)

    def _on_progress_seek(self, value):
        """진행 슬라이더 seek"""
        try:
            duration = self.engine.get_duration()
            if duration > 0:
                seek_time = float(value) * duration / 100.0
                self.engine.seek(seek_time)
        except:
            pass

    def _on_mr_volume(self, value):
        """MR 볼륨"""
        if not self._ui_ready:
            return
        vol = float(value)
        self.engine.set_mr_volume(vol)
        self.label_mr_vol.config(text=f"{vol:.2f}")
        # 다른 슬라이더와 동기화 (값이 다르면만 set → 무한 재귀 방지)
        if abs(self.slider_mr_vol_bar.get() - vol) > 1e-6:
            self.slider_mr_vol_bar.set(vol)

    def _on_mic_volume(self, value):
        """마이크 볼륨"""
        if not self._ui_ready:
            return
        vol = float(value)
        self.engine.set_mic_volume(vol)

    def _on_monitor_volume(self, value):
        """모니터 볼륨"""
        if not self._ui_ready:
            return
        vol = float(value)
        self.engine.set_monitor_volume(vol)

    def _on_agc_toggle(self):
        """AGC 토글"""
        if not self._ui_ready:
            return
        enabled = self.var_agc_enabled.get()
        self.engine.set_agc_enabled(enabled)
        self._save_settings()

    def _on_echo_preset(self, event):
        """에코 프리셋"""
        if not self._ui_ready:
            return
        preset = self.combo_echo.get()

        preset_map = {
            "끄기": "끄기",
            "노래방 (기본)": "노래방",
            "콘서트홀": "콘서트홀",
            "동굴": "동굴"
        }

        presets = {
            "끄기": {"delay": 0.05, "feedback": 0.0, "mix": 0.0, "reverb": 0.0},
            "노래방 (기본)": {"delay": 0.25, "feedback": 0.45, "mix": 0.32, "reverb": 0.40},
            "콘서트홀": {"delay": 0.30, "feedback": 0.50, "mix": 0.35, "reverb": 0.50},
            "동굴": {"delay": 0.40, "feedback": 0.60, "mix": 0.45, "reverb": 0.55},
        }

        if preset in presets:
            p = presets[preset]
            self.slider_delay.set(p["delay"])
            self.slider_feedback.set(p["feedback"])
            self.slider_echo_mix.set(p["mix"])
            self.slider_reverb.set(p["reverb"])

        engine_preset = preset_map.get(preset, preset)
        self.engine.set_echo_preset(engine_preset)

        self._save_settings()

    def _on_delay_change(self, value):
        """에코 딜레이"""
        if not self._ui_ready:
            return
        delay = float(value)
        feedback = self.slider_feedback.get()
        mix = self.slider_echo_mix.get()
        self.engine.set_delay_effect(delay, feedback, mix)

    def _on_feedback_change(self, value):
        """에코 피드백"""
        if not self._ui_ready:
            return
        delay = self.slider_delay.get()
        feedback = float(value)
        mix = self.slider_echo_mix.get()
        self.engine.set_delay_effect(delay, feedback, mix)

    def _on_echo_mix_change(self, value):
        """에코 믹스"""
        if not self._ui_ready:
            return
        delay = self.slider_delay.get()
        feedback = self.slider_feedback.get()
        mix = float(value)
        self.engine.set_delay_effect(delay, feedback, mix)

    def _on_reverb_change(self, value):
        """리버브"""
        if not self._ui_ready:
            return
        reverb = float(value)
        preset = self.combo_echo.get()
        preset_dry = {
            "끄기": 0.75,
            "노래방 (기본)": 0.80,
            "콘서트홀": 0.75,
            "동굴": 0.70,
        }.get(preset, 0.75)
        self.engine.set_reverb_effect(reverb, reverb, preset_dry)

    def _on_voice_correction_toggle(self):
        """목소리 보정 토글"""
        if not self._ui_ready:
            return
        enabled = self.var_voice_correction_enabled.get()

        strength_map = {
            "약": "weak",
            "중 (기본)": "medium",
            "강": "strong"
        }
        strength_text = self.combo_voice_strength.get()
        strength = strength_map.get(strength_text, "medium")

        self.engine.set_voice_correction(enabled, strength)
        self._save_settings()

    def _on_voice_correction_strength_change(self, event):
        """목소리 보정 강도 변경"""
        if not self._ui_ready:
            return

        strength_map = {
            "약": "weak",
            "중 (기본)": "medium",
            "강": "strong"
        }
        strength_text = self.combo_voice_strength.get()
        strength = strength_map.get(strength_text, "medium")

        enabled = self.var_voice_correction_enabled.get()
        self.engine.set_voice_correction(enabled, strength)
        self._save_settings()

    def _on_key_down(self):
        """키 낮추기"""
        current = self.engine.get_pitch_shift_semitones()
        self.engine.shift_pitch(current - 1)

    def _on_key_up(self):
        """키 높이기"""
        current = self.engine.get_pitch_shift_semitones()
        self.engine.shift_pitch(current + 1)

    def _on_tempo_down(self):
        """템포 낮추기"""
        current = self.engine.get_tempo_stretch_factor()
        self.engine.set_tempo(current - 0.1)

    def _on_tempo_up(self):
        """템포 높이기"""
        current = self.engine.get_tempo_stretch_factor()
        self.engine.set_tempo(current + 0.1)

    def _on_reset_all(self):
        """키+템포 리셋"""
        self.engine.reset_pitch_and_tempo()

    def _update_loop(self):
        """주기적 갱신 (200ms)"""
        if not self.is_running:
            return

        try:
            while not self.engine.error_queue.empty():
                error_msg = self.engine.error_queue.get_nowait()
                print(f"[ERROR] {error_msg}")

            current = self.engine.get_current_time()
            duration = self.engine.get_duration()
            if duration > 0:
                progress = (current / duration) * 100
                # 값이 크게 다르면만 set (사용자 드래그 중일 때 방해 방지)
                if abs(self.slider_progress.get() - progress) > 1.0:
                    self.slider_progress.set(progress)

            current_mm_ss = self._format_time(current)
            duration_mm_ss = self._format_time(duration)
            self.label_time.config(text=f"{current_mm_ss} / {duration_mm_ss}")

            semitones = self.engine.get_pitch_shift_semitones()
            if self.engine.is_pitch_shifting_in_progress():
                self.label_key.config(text=f"키: {semitones:+d} (변환 중...)")
            else:
                self.label_key.config(text=f"키: {semitones:+d}")

            tempo = self.engine.get_tempo_stretch_factor()
            if self.engine.is_tempo_rendering_in_progress():
                self.label_tempo.config(text=f"템포: {tempo:.2f}x (변환 중...)")
            else:
                self.label_tempo.config(text=f"템포: {tempo:.2f}x")

            mic_rms = self.engine.last_mic_rms
            dbfs = 20 * np.log10(max(mic_rms, 1e-9))
            meter_pct = max(0, min(100, (dbfs + 60) / 60 * 100))
            self.progressbar_mic_level.config(value=meter_pct)

            if self.engine.input_stream is not None and self.engine.input_stream.active:
                if mic_rms < 1e-5:
                    self.silent_mic_count += 1
                    if self.silent_mic_count >= 25 and not self.silent_mic_warning_shown:
                        warning_text = "⚠ 마이크에서 소리가 감지되지 않습니다 — 마이크 선택/음소거 확인하세요"
                        self.label_status.config(text=warning_text, foreground="orange")
                        self.silent_mic_warning_shown = True
                else:
                    if self.silent_mic_count > 0 or self.silent_mic_warning_shown:
                        self.silent_mic_count = 0
                        self.silent_mic_warning_shown = False
                        self._update_status()

            agc_gain = self.engine.get_agc_gain()
            self.label_agc_gain.config(text=f"x{agc_gain:.1f}")

            if self.engine.is_recording:
                recording_sec = self.engine.get_recording_seconds()
                recording_time_str = self._format_time(recording_sec)
                self.label_recording_status.config(text=f"● 녹음 중 {recording_time_str}", foreground="red")

            import time
            current_time = time.time()
            if current_time - self.last_stats_display_time >= 5:
                self.last_stats_display_time = current_time

                self._update_status()

                if int(current_time / 5) % 6 == 0:
                    stats = self.engine.get_stats()
                    total_xrun = stats.get('input_callback_xrun', 0) + stats.get('output_callback_xrun', 0)
                    underrun = stats.get('mic_underrun', 0)

                    if total_xrun > 0 or underrun > 0:
                        status_text = f"⚠ 끊김 감지 {total_xrun + underrun}회 — 사용법.txt 문제해결 참고"
                        self.label_status.config(text=status_text, foreground="red")
                        print(f"[STATS] input_xrun={stats.get('input_callback_xrun', 0)}, output_xrun={stats.get('output_callback_xrun', 0)}, underrun={underrun}")

        except Exception as e:
            pass

        self.root.after(200, self._update_loop)

    def _format_time(self, seconds: float) -> str:
        """초 → mm:ss"""
        total_secs = int(seconds)
        mins = total_secs // 60
        secs = total_secs % 60
        return f"{mins:02d}:{secs:02d}"

    def _on_youtube_download(self):
        """유튜브 다운로드"""
        url = self.entry_yt_url.get().strip()
        if not url:
            self._show_error("유튜브 URL을 입력하세요.")
            return

        try:
            parsed = urlparse(url)
            if 'youtube' not in parsed.netloc and 'youtu.be' not in parsed.netloc:
                self._show_error("올바른 유튜브 URL을 입력하세요.")
                return
        except:
            self._show_error("올바른 유튜브 URL을 입력하세요.")
            return

        if not self.mr_folder:
            self._show_error("먼저 MR 폴더를 선택하세요.")
            return

        self.btn_yt_download.config(state=tk.DISABLED)
        self.label_yt_status.config(text="다운로드 중...", foreground="blue")
        self.entry_yt_url.config(state=tk.DISABLED)

        thread = threading.Thread(
            target=self._youtube_download_thread,
            args=(url,),
            daemon=True
        )
        thread.start()

    def _youtube_progress_hook(self, d):
        """yt-dlp 진행 콜백"""
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '?')
            speed = d.get('_speed_str', '?')
            eta = d.get('_eta_str', '?')
            status_text = f"다운로드 중: {percent} @ {speed} (예상 시간: {eta})"
            self.label_yt_status.config(text=status_text, foreground="blue")
        elif d['status'] == 'finished':
            self.label_yt_status.config(text="후처리 중...", foreground="blue")

    def _youtube_download_thread(self, url: str):
        """유튜브 다운로드 스레드"""
        try:
            downloaded_path = self.engine.download_youtube_mr(
                url,
                self.mr_folder,
                progress_callback=self._youtube_progress_hook
            )

            if downloaded_path:
                self.root.after(0, self._refresh_youtube_download_complete, downloaded_path)
            else:
                self.root.after(0, self._refresh_youtube_download_error)

        except Exception as e:
            self.root.after(0, self._refresh_youtube_download_error, str(e))

    def _refresh_youtube_download_complete(self, file_path: str):
        """유튜브 다운로드 완료 처리"""
        try:
            self._load_mr_files()

            file_name = Path(file_path).stem
            for i, file in enumerate(self.mr_files):
                if file.stem == file_name:
                    self.mr_search_var.set("")
                    self.listbox_mr.selection_clear(0, tk.END)
                    self.listbox_mr.selection_set(i)
                    self.listbox_mr.see(i)
                    if self.engine.load_mr(str(file)):
                        self.label_current.config(text=file_name)
                        self.engine.play()
                    break

            self.entry_yt_url.delete(0, tk.END)
            self.label_yt_status.config(text="다운로드 완료!", foreground="green")
            self.btn_yt_download.config(state=tk.NORMAL)
            self.entry_yt_url.config(state=tk.NORMAL)

        except Exception as e:
            self.label_yt_status.config(text=f"오류: {str(e)}", foreground="red")
            self.btn_yt_download.config(state=tk.NORMAL)
            self.entry_yt_url.config(state=tk.NORMAL)

    def _refresh_youtube_download_error(self, error_msg: str = ""):
        """유튜브 다운로드 오류 처리"""
        msg = error_msg if error_msg else "다운로드 실패"
        self.label_yt_status.config(text=f"오류: {msg}", foreground="red")
        self.btn_yt_download.config(state=tk.NORMAL)
        self.entry_yt_url.config(state=tk.NORMAL)

    def _show_error(self, message: str):
        """에러 메시지 표시"""
        messagebox.showerror("오류", message)

    def _on_recording_toggle(self):
        """녹음 토글"""
        if not self.engine.input_stream or not self.engine.input_stream.active:
            self._show_error("스트림이 동작 중이 아닙니다. 장치를 확인하세요.")
            return

        if self.engine.is_recording:
            duration = self.engine.stop_recording()
            self.last_recording_duration = duration

            self.btn_recording.config(text="● 녹음")
            if self.last_recording_filepath:
                file_name = Path(self.last_recording_filepath).name
                duration_str = self._format_time(duration)
                self.label_recording_status.config(
                    text=f"저장됨: {file_name} ({duration_str})",
                    foreground="green"
                )
                self.btn_open_recording.config(state=tk.NORMAL)
            else:
                self.label_recording_status.config(text="", foreground="gray")
        else:
            try:
                self.recording_folder.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self._show_error(f"녹음 폴더 생성 실패: {e}")
                return

            now = datetime.now()
            filename = f"녹음_{now.strftime('%Y-%m-%d_%H-%M-%S')}.wav"
            filepath = self.recording_folder / filename

            if self.engine.start_recording(str(filepath)):
                self.last_recording_filepath = str(filepath)
                self.btn_recording.config(text="■ 정지")
                self.label_recording_status.config(text="● 녹음 중 00:00", foreground="red")
                self.btn_open_recording.config(state=tk.DISABLED)
            else:
                self._show_error("녹음 시작 실패")

    def _on_open_recording_folder(self):
        """녹음 폴더 열기"""
        try:
            if self.recording_folder.exists():
                if os.name == 'nt':
                    os.startfile(str(self.recording_folder))
                else:
                    import subprocess
                    subprocess.Popen(['open', str(self.recording_folder)])
            else:
                self._show_error("녹음 폴더가 없습니다")
        except Exception as e:
            self._show_error(f"폴더 열기 실패: {e}")

    def _on_auto_detect_mic(self):
        """[마이크 자동 감지] 버튼 클릭"""
        if self.auto_detect_mic_in_progress:
            messagebox.showwarning("안내", "마이크 감지가 이미 진행 중입니다.")
            return

        self.auto_detect_mic_in_progress = True
        self.label_status.config(text="마이크 찾는 중... (아무 말이나 해보세요)", foreground="blue")
        self.root.update_idletasks()

        thread = threading.Thread(
            target=self._auto_detect_mic_thread,
            daemon=True
        )
        thread.start()

    def _auto_detect_mic_thread(self):
        """자동 감지 스레드: 각 입력 장치 테스트 및 최고 RMS 찾기"""
        import sounddevice as sd
        try:
            input_devs, _ = self.engine.list_devices()

            was_playing = self.engine.mr_playing
            self.engine._stop_stream()

            best_device = None
            best_rms = 0.0
            test_duration = 1.2
            test_samples = int(48000 * test_duration)

            for dev in input_devs:
                if dev['hostapi'] not in ['MME', 'WASAPI']:
                    continue

                try:
                    device_info = sd.query_devices(dev['index'])
                    device_sr = int(device_info['default_samplerate'])

                    test_audio = sd.rec(test_samples, samplerate=device_sr, channels=1, device=dev['index'])
                    sd.wait()

                    test_rms = float(np.sqrt(np.mean(test_audio ** 2)))

                    if test_rms > best_rms:
                        best_rms = test_rms
                        best_device = dev

                except Exception as e:
                    pass

            try:
                mic_idx = self._get_selected_device_index(self.device_input_var, is_input=True)
                out_idx = self._get_selected_device_index(self.device_output_var, is_input=False)
                monitor_name = self.device_monitor_var.get()
                monitor_idx = None if monitor_name == "사용 안 함" else self._get_selected_device_index(self.device_monitor_var, is_input=False)
                self.engine.start_stream(mic_idx, out_idx, monitor_idx)
            except:
                pass

            if best_device is not None and best_rms >= 1e-4:
                self.root.after(0, self._auto_detect_mic_complete, best_device, best_rms, was_playing)
            else:
                self.root.after(0, self._auto_detect_mic_not_found, was_playing)

        except Exception as e:
            self.root.after(0, self._auto_detect_mic_error, str(e), was_playing)

    def _auto_detect_mic_complete(self, device: dict, rms: float, was_playing: bool):
        """자동 감지 완료: 장치 선택 및 표시"""
        try:
            device_display = f"{device['name']} ({device['hostapi']})"

            combo_values = self.combo_input['values']
            if device_display in combo_values:
                self.device_input_var.set(device_display)
                self._start_stream()

                rms_display = f"{rms:.4f}"
                self.label_status.config(text=f"감지됨: {device['name']} (신호 {rms_display})", foreground="green")
            else:
                self.label_status.config(text=f"장치를 찾았으나 콤보에 없습니다: {device['name']}", foreground="red")

        except Exception as e:
            self.label_status.config(text=f"오류: {str(e)}", foreground="red")
        finally:
            self.auto_detect_mic_in_progress = False

    def _auto_detect_mic_not_found(self, was_playing: bool):
        """자동 감지 미성공: 어느 마이크에서도 소리 감지 안 됨"""
        self.label_status.config(
            text="어느 마이크에서도 소리가 감지되지 않았습니다 — 연결/음소거를 확인하세요",
            foreground="red"
        )
        self.auto_detect_mic_in_progress = False

    def _auto_detect_mic_error(self, error_msg: str, was_playing: bool):
        """자동 감지 오류"""
        self.label_status.config(text=f"자동 감지 오류: {error_msg}", foreground="red")
        self.auto_detect_mic_in_progress = False

    def _on_closing(self):
        """종료"""
        self.is_running = False
        if self.video_panel:
            try:
                self.video_panel.cleanup()
            except:
                pass
        if self.video_window:
            try:
                self.video_window._on_close()
            except:
                pass
        self._save_settings()
        self.engine.shutdown()
        self.root.destroy()


def run_selftest():
    """--selftest 모드: GUI 없이 설정 확인"""
    import sys
    from pathlib import Path
    from audio_engine import AudioEngine, get_ffmpeg_path
    import subprocess

    log_path = Path(sys.executable).parent / "selftest.log"
    results = []

    def log_msg(msg: str):
        results.append(msg)
        print(msg)

    try:
        log_msg("[OK] tkinter imported")
        log_msg("[OK] cv2 (opencv) imported")
        log_msg("[OK] PIL imported")

        try:
            import yt_dlp
            log_msg("[OK] yt-dlp imported")
        except ImportError:
            log_msg("[WARN] yt-dlp not found (youtube download will fail)")

        try:
            import pedalboard
            log_msg("[OK] pedalboard imported")
        except ImportError:
            log_msg("[ERROR] pedalboard import failed")
            raise

        try:
            import sounddevice
            log_msg("[OK] sounddevice imported")
        except ImportError:
            log_msg("[ERROR] sounddevice import failed")
            raise

        try:
            import numpy
            log_msg("[OK] numpy imported")
        except ImportError:
            log_msg("[ERROR] numpy import failed")
            raise

        try:
            engine = AudioEngine()
            input_devs, output_devs = engine.list_devices()
            log_msg(f"[OK] AudioEngine created: {len(input_devs)} input, {len(output_devs)} output devices")
        except Exception as e:
            log_msg(f"[ERROR] AudioEngine creation failed: {e}")
            raise

        try:
            ffmpeg_path = get_ffmpeg_path()
            startup_info = subprocess.STARTUPINFO() if os.name == 'nt' else None
            if os.name == 'nt' and startup_info:
                startup_info.dwFlags |= 0x08000000
            result = subprocess.run(
                [ffmpeg_path, '-version'],
                capture_output=True,
                text=True,
                startupinfo=startup_info
            )

            if result.returncode == 0:
                version_line = result.stdout.split('\n')[0]
                log_msg(f"[OK] FFmpeg: {ffmpeg_path} - {version_line}")
            else:
                log_msg(f"[ERROR] FFmpeg check failed: {result.stderr}")
                raise Exception("FFmpeg version check failed")
        except Exception as e:
            log_msg(f"[ERROR] FFmpeg: {e}")
            raise

        log_msg("[OK] All tests passed!")

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(results))

        sys.exit(0)

    except Exception as e:
        log_msg(f"[FATAL] Selftest failed: {e}")
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(results))
        sys.exit(1)


if __name__ == "__main__":
    import sys
    import subprocess
    import os

    if '--selftest' in sys.argv:
        run_selftest()

    root = tk.Tk()
    app = DiscordKaraoke(root)
    root.mainloop()
