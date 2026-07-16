"""
디스코드 노래방 오디오 엔진
마이크 입력 + MR 믹싱 + 이펙트 체인 처리
"""

import sounddevice as sd
import numpy as np
import pedalboard as pb
from pedalboard import time_stretch
from pedalboard.io import AudioFile
import threading
import queue
from collections import deque
from typing import Tuple, List, Optional, Dict
import traceback
import subprocess
import tempfile
import os
import sys
from pathlib import Path
import wave
import time


def get_ffmpeg_path() -> str:
    """포터블(frozen) 실행 시 exe 옆에 동봉된 ffmpeg.exe 우선, 아니면 PATH의 ffmpeg"""
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        for cand in (exe_dir / 'ffmpeg.exe', exe_dir / 'ffmpeg' / 'ffmpeg.exe'):
            if cand.exists():
                return str(cand)
    return 'ffmpeg'


class AudioEngine:
    """오디오 엔진 - 분리 스트림(입력/출력), 지터 버퍼, MR 로드/재생, 이펙트 처리"""

    def __init__(self, sample_rate: int = 48000, block_size: int = 512):
        self.sample_rate = sample_rate
        self.block_size = block_size

        # 지연 모드 설정 ('low' / 'balanced' / 'stable')
        self.latency_mode = 'balanced'  # 기본값: balanced

        # 지연 모드별 파라미터 정의
        self.latency_mode_params = {
            'low': {
                'blocksize': 256,
                'latency': 'low',
                'jitter_depth': 2,
                'jitter_drop_threshold': 5,
                'monitor_prefill': 2,
                'agc_alpha_scale': 256 / 1024,
            },
            'balanced': {
                'blocksize': 512,
                'latency': 'low',
                'jitter_depth': 3,
                'jitter_drop_threshold': 6,
                'monitor_prefill': 3,
                'agc_alpha_scale': 512 / 1024,
            },
            'stable': {
                'blocksize': 1024,
                'latency': 'high',
                'jitter_depth': 6,
                'jitter_drop_threshold': 10,
                'monitor_prefill': 2,
                'agc_alpha_scale': 1.0,
            },
        }

        # 현재 적용된 모드의 파라미터
        self._current_latency_params = self.latency_mode_params[self.latency_mode]

        # 오디오 스트림 (분리: 입력 + 출력)
        self.input_stream = None  # 마이크 입력
        self.output_stream = None  # 메인 출력
        self.monitor_stream = None

        # 스트림이 열린 호스트API 정보 저장 (상태 표시용)
        self.input_hostapi_name = "Unknown"
        self.output_hostapi_name = "Unknown"

        # MR(반주) 버퍼 및 재생 상태
        self.mr_buffer = None  # shape: (frames, 2) 스테레오
        self.mr_position = 0  # 현재 재생 위치(프레임 인덱스)
        self.mr_playing = False
        self.mr_volume = 1.0
        self.mr_original_path = None  # 원본 MR 파일 경로

        # 키 조절 상태
        self.pitch_shift_semitones = 0
        self.is_pitch_shifting = False  # 백그라운드 렌더 중 플래그
        self.pitch_shift_thread = None
        self.pitch_render_generation = 0  # 세대 카운터 (stale 렌더 폐기용)

        # 템포 조절 상태
        self.tempo_stretch_factor = 1.0  # 0.5~1.5
        self.is_tempo_stretching = False  # 백그라운드 렌더 중 플래그
        self.tempo_stretch_thread = None
        self.tempo_render_generation = 0  # 세대 카운터

        # 마이크 및 이펙트
        self.mic_volume = 1.0
        # 에코 프리셋 강화: 노래방 프리셋으로 엔진 기본값 일치
        self.delay_effect = pb.Delay(delay_seconds=0.25, feedback=0.45, mix=0.32)
        self.reverb_effect = pb.Reverb(room_size=0.55, wet_level=0.40, dry_level=0.80)

        # 목소리 보정 체인 (Delay/Reverb 이전)
        self.voice_correction_enabled = False
        self.voice_correction_strength = "medium"  # "weak" / "medium" / "strong"
        self.voice_correction_chain = []  # NoiseGate, HighpassFilter, Compressor, Gain 등

        # 효과 체인: 마이크 입력 → [보정] → [Delay + Reverb]
        self.effects_chain = [self.delay_effect, self.reverb_effect]

        # 블록 피크 리미터 (tanh 대체)
        self.limiter_gain = 1.0

        # AGC (자동 음량 조절)
        self.agc_enabled = True
        self.agc_gain = 1.0
        self.agc_gain_prev = 1.0  # 지퍼 노이즈 방지용 (게인 램프)
        self.agc_target_rms = 0.08
        self.agc_gate_threshold = 1e-3
        self.agc_gain_min = 0.5
        self.agc_gain_max = 4.0  # 8.0 → 4.0: 노이즈 플로어 히스 감소
        self.agc_attack_alpha = 0.03  # 게인 상승 시 느린 속도
        self.agc_release_alpha = 0.25  # 게인 하강 시 빠른 속도
        self.agc_last_rms = 0.0  # 게이트 닫힘 시 이완용
        self.last_mic_rms = 0.0  # 레벨 미터용

        # 모니터 출력용 블록 단위 버퍼 (재설계)
        self.monitor_buffer = deque(maxlen=8)  # 블록 단위 저장 (≈170ms)
        self.monitor_partial_block = np.zeros((0, 2), dtype=np.float32)  # 부분 블록 보관
        self.monitor_volume = 1.0

        # 녹음 기능
        self.is_recording = False
        self.recording_queue = queue.Queue(maxsize=32)  # 최종 믹스 블록 저장 (thread-safe, 32 블록 = ~680ms)
        self.recording_filepath = None
        self.recording_thread = None
        self.recording_stop_event = threading.Event()
        self.recording_start_time = None  # 녹음 시작 시각

        # 마이크 입력 지터 버퍼 (클럭 드리프트 보상)
        # 지연 모드별 깊이로 동적 할당
        self.mic_jitter_buffer = deque(maxlen=self._current_latency_params['jitter_depth'])

        # 지터 버퍼 깊이 모니터링 (연속 초과 카운트)
        self.jitter_depth_excess_count = 0  # (목표+2) 초과 연속 횟수
        self.jitter_last_depth = 0

        # 통계 카운터
        self.stats = {
            'mic_underrun': 0,      # 마이크 버퍼 비움 카운트
            'mic_overflow_drop': 0, # 마이크 버퍼 넘침으로 인한 드롭 카운트
            'input_callback_xrun': 0,  # 입력 콜백 xrun 플래그 발생 횟수
            'output_callback_xrun': 0,  # 출력 콜백 xrun 플래그 발생 횟수
            'jitter_adaptive_drop': 0,  # 지터 적응형 drop 횟수
        }

        # 에러 큐
        self.error_queue = queue.Queue()

        # 스레드 안전성
        self.lock = threading.Lock()

    def _resolve_to_wasapi(self, device_index: int) -> Tuple[int, str]:
        """
        주어진 장치 인덱스가 MME인 경우 같은 이름의 WASAPI 장치로 해석
        Returns: (resolved_index, hostapi_name)
        """
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()

            if not (0 <= device_index < len(devices)):
                return device_index, "Unknown"

            source_dev = devices[device_index]
            source_api_idx = source_dev['hostapi']
            source_api_name = hostapis[source_api_idx]['name'] if source_api_idx < len(hostapis) else 'Unknown'
            source_name = source_dev['name']

            # 이미 WASAPI면 그대로 반환
            if 'WASAPI' in source_api_name:
                return device_index, source_api_name

            # MME인 경우: 같은 이름의 WASAPI 장치 찾기
            # (MME는 31자로 잘리므로 접두사 매칭 사용)
            for i, dev in enumerate(devices):
                dev_api_idx = dev['hostapi']
                dev_api_name = hostapis[dev_api_idx]['name'] if dev_api_idx < len(hostapis) else 'Unknown'
                dev_name = dev['name']

                # WASAPI 장치이고 이름이 매칭하면
                if 'WASAPI' in dev_api_name:
                    # 정확한 매칭 또는 접두사 매칭
                    if dev_name == source_name or source_name.startswith(dev_name[:min(31, len(dev_name))]):
                        return i, dev_api_name

            # WASAPI 장치를 찾지 못하면 원본 반환
            return device_index, source_api_name

        except Exception as e:
            self.error_queue.put(f"WASAPI 해석 실패: {str(e)}")
            return device_index, "Unknown"

    def list_devices(self) -> Tuple[List[Dict], List[Dict]]:
        """
        입출력 장치 목록 반환
        Returns: (input_devices, output_devices)
        각 항목: {'index': int, 'name': str, 'hostapi': str}
        """
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()

            input_devs = []
            output_devs = []

            for i, dev in enumerate(devices):
                hostapi_name = hostapis[dev['hostapi']]['name'] if dev['hostapi'] < len(hostapis) else 'Unknown'

                if dev['max_input_channels'] > 0:
                    input_devs.append({
                        'index': i,
                        'name': dev['name'],
                        'hostapi': hostapi_name
                    })

                if dev['max_output_channels'] > 0:
                    output_devs.append({
                        'index': i,
                        'name': dev['name'],
                        'hostapi': hostapi_name
                    })

            return input_devs, output_devs
        except Exception as e:
            self.error_queue.put(f"장치 목록 조회 실패: {str(e)}")
            return [], []

    def get_default_devices(self) -> Tuple[int, int, int]:
        """
        기본 장치 인덱스 반환: (mic_index, output_index, monitor_index)
        output_index: "CABLE Input" 포함 → 없으면 시스템 기본 출력
        monitor_index: 시스템 기본 출력
        """
        try:
            input_devs, output_devs = self.list_devices()

            # 마이크: 기본 입력
            mic_idx = sd.default.device[0]

            # 출력: "CABLE Input" 우선
            output_idx = sd.default.device[1]
            for dev in output_devs:
                if 'CABLE Input' in dev['name']:
                    output_idx = dev['index']
                    break

            # 모니터: 시스템 기본 출력
            monitor_idx = sd.default.device[1]

            return int(mic_idx), int(output_idx), int(monitor_idx)
        except Exception as e:
            self.error_queue.put(f"기본 장치 설정 실패: {str(e)}")
            return 0, 0, 0

    def start_stream(self, mic_device: int, output_device: int, monitor_device: Optional[int] = None):
        """
        오디오 스트림 시작 (분리 입력/출력)
        mic_device: 마이크 입력 장치 인덱스
        output_device: 메인 출력 장치 인덱스 (VB-Cable 등)
        monitor_device: 모니터 출력 장치 (None이면 사용 안 함)
        """
        try:
            self._stop_stream()

            # 현재 지연 모드 파라미터 적용
            params = self._current_latency_params
            blocksize = params['blocksize']
            latency_mode = params['latency']
            jitter_max_depth = params['jitter_depth']

            # 블록 크기 업데이트
            self.block_size = blocksize

            # 지터 버퍼 재생성 (새로운 maxlen)
            self.mic_jitter_buffer = deque(maxlen=jitter_max_depth)
            self.jitter_depth_excess_count = 0
            self.jitter_last_depth = 0

            # 통계 리셋
            self.stats = {
                'mic_underrun': 0,
                'mic_overflow_drop': 0,
                'input_callback_xrun': 0,
                'output_callback_xrun': 0,
                'jitter_adaptive_drop': 0,
            }

            # WASAPI 해석: MME → WASAPI 매핑
            mic_device_resolved, mic_api_name = self._resolve_to_wasapi(mic_device)
            output_device_resolved, output_api_name = self._resolve_to_wasapi(output_device)
            self.input_hostapi_name = mic_api_name
            self.output_hostapi_name = output_api_name

            # 입력 스트림 (마이크) - 지터 버퍼 채우는 역할
            # WASAPI 해석 적용, 실패 시 원본으로 폴백
            try:
                self.input_stream = sd.InputStream(
                    device=mic_device_resolved,
                    samplerate=self.sample_rate,
                    blocksize=blocksize,
                    channels=2,
                    dtype=np.float32,
                    latency=latency_mode,
                    callback=self._input_callback
                )
                self.input_stream.start()
            except Exception as e:
                # 폴백: 원본 장치 인덱스로 재시도
                self.error_queue.put(f"입력 WASAPI 해석 실패, 폴백 시도: {str(e)}")
                self.input_hostapi_name = "Fallback"
                self.input_stream = sd.InputStream(
                    device=mic_device,
                    samplerate=self.sample_rate,
                    blocksize=blocksize,
                    channels=2,
                    dtype=np.float32,
                    latency=latency_mode,
                    callback=self._input_callback
                )
                self.input_stream.start()

            # 출력 스트림 (메인 출력) - 지터 버퍼에서 pop + MR 믹싱
            try:
                self.output_stream = sd.OutputStream(
                    device=output_device_resolved,
                    samplerate=self.sample_rate,
                    blocksize=blocksize,
                    channels=2,
                    dtype=np.float32,
                    latency=latency_mode,
                    callback=self._output_callback
                )
                self.output_stream.start()
            except Exception as e:
                # 폴백: 원본 장치 인덱스로 재시도
                self.error_queue.put(f"출력 WASAPI 해석 실패, 폴백 시도: {str(e)}")
                self.output_hostapi_name = "Fallback"
                self.output_stream = sd.OutputStream(
                    device=output_device,
                    samplerate=self.sample_rate,
                    blocksize=blocksize,
                    channels=2,
                    dtype=np.float32,
                    latency=latency_mode,
                    callback=self._output_callback
                )
                self.output_stream.start()

            # 모니터 스트림 (선택적)
            if monitor_device is not None and monitor_device != output_device:
                try:
                    monitor_device_resolved, _ = self._resolve_to_wasapi(monitor_device)
                    self.monitor_stream = sd.OutputStream(
                        device=monitor_device_resolved,
                        samplerate=self.sample_rate,
                        blocksize=blocksize,
                        channels=2,
                        dtype=np.float32,
                        latency=latency_mode,
                        callback=self._monitor_callback
                    )
                except:
                    # 모니터 장치 실패 시 원본으로 재시도
                    self.monitor_stream = sd.OutputStream(
                        device=monitor_device,
                        samplerate=self.sample_rate,
                        blocksize=blocksize,
                        channels=2,
                        dtype=np.float32,
                        latency=latency_mode,
                        callback=self._monitor_callback
                    )
                self.monitor_stream.start()

                # 초기 언더런 방지: 프리필 블록 수는 지연 모드에 따라 결정
                prefill_count = params['monitor_prefill']
                zero_block = np.zeros((self.block_size, 2), dtype=np.float32)
                for _ in range(prefill_count):
                    self.monitor_buffer.append(zero_block.copy())
        except Exception as e:
            self.error_queue.put(f"스트림 시작 실패: {str(e)}")
            raise

    def _stop_stream(self):
        """스트림 종료"""
        if self.input_stream is not None:
            try:
                self.input_stream.stop()
                self.input_stream.close()
            except:
                pass
            self.input_stream = None

        if self.output_stream is not None:
            try:
                self.output_stream.stop()
                self.output_stream.close()
            except:
                pass
            self.output_stream = None

        if self.monitor_stream is not None:
            try:
                self.monitor_stream.stop()
                self.monitor_stream.close()
            except:
                pass
            self.monitor_stream = None

    def _input_callback(self, indata, frames, time, status):
        """입력 콜백 (마이크) - 이펙트/AGC 처리 후 지터 버퍼에 push"""
        try:
            if status:
                self.stats['input_callback_xrun'] += 1
                self.error_queue.put(f"입력 콜백 상태: {status}")

            # 마이크 입력 처리
            mic_input = indata.copy()  # shape: (frames, 2) or (frames, 1)

            # 모노면 스테레오로 복제
            if mic_input.shape[1] == 1:
                mic_input = np.tile(mic_input, (1, 2))

            # AGC 전 마이크 RMS 측정 (게이트 판정용)
            mic_rms = np.sqrt(np.mean(mic_input ** 2))
            self.last_mic_rms = mic_rms

            # Pedalboard 이펙트 체인 (shape: (channels, frames))
            mic_processed = mic_input.T.astype(np.float32)  # (2, frames)

            # 목소리 보정 체인 (Delay/Reverb 이전)
            for effect in self.voice_correction_chain:
                mic_processed = effect(mic_processed, self.sample_rate, reset=False)

            # 에코/리버브 이펙트
            for effect in self.effects_chain:
                mic_processed = effect(mic_processed, self.sample_rate, reset=False)

            mic_processed = mic_processed.T  # (frames, 2)

            # AGC 처리 (블록 크기에 따라 알파값 스케일링)
            if self.agc_enabled:
                # 블록 크기에 따른 알파 보정 (기준: 1024샘플)
                alpha_scale = self._current_latency_params['agc_alpha_scale']
                agc_attack_alpha_scaled = 1.0 - (1.0 - self.agc_attack_alpha) ** alpha_scale
                agc_release_alpha_scaled = 1.0 - (1.0 - self.agc_release_alpha) ** alpha_scale

                if mic_rms > self.agc_gate_threshold:
                    # 게이트 열림: 목표 RMS에 맞춰 게인 갱신
                    desired_gain = self.agc_target_rms / max(mic_rms, 1e-6)
                    desired_gain = max(self.agc_gain_min, min(self.agc_gain_max, desired_gain))

                    # 부드러운 추종: 상승은 느리게(attack), 하강은 빠르게(release)
                    if desired_gain > self.agc_gain:
                        alpha = agc_attack_alpha_scaled
                    else:
                        alpha = agc_release_alpha_scaled

                    self.agc_gain += (desired_gain - self.agc_gain) * alpha
                    self.agc_last_rms = mic_rms
                else:
                    # 게이트 닫힘: 게인을 1.0으로 아주 천천히 이완 (잡음 플로어 증폭 방지)
                    self.agc_gain += (1.0 - self.agc_gain) * 0.005

            # 마이크 볼륨 + AGC 배율 적용 (게인 램프 적용)
            # 지퍼 노이즈 방지: 이전 AGC 게인에서 현재 게인으로 선형 보간
            gain_ramp = np.linspace(self.agc_gain_prev, self.agc_gain, frames)[:, None]  # (frames, 1)
            mic_output = mic_processed * self.mic_volume * gain_ramp
            self.agc_gain_prev = self.agc_gain  # 다음 블록을 위해 저장

            # 처리된 마이크 블록을 지터 버퍼에 push
            mic_block = mic_output.astype(np.float32)

            # 지터 버퍼 체크: 지연 모드별 drop_threshold 초과 시 드롭 (클럭 드리프트 보상)
            drop_threshold = self._current_latency_params['jitter_drop_threshold']
            if len(self.mic_jitter_buffer) >= drop_threshold:
                self.mic_jitter_buffer.popleft()
                self.stats['mic_overflow_drop'] += 1

            self.mic_jitter_buffer.append(mic_block)

        except Exception as e:
            self.error_queue.put(f"입력 콜백 예외: {str(e)}\n{traceback.format_exc()}")

    def _output_callback(self, outdata, frames, time, status):
        """출력 콜백 (메인) - 지터 버퍼 pop + MR 믹싱 + 리미터 + 모니터 버퍼 push"""
        try:
            if status:
                self.stats['output_callback_xrun'] += 1
                self.error_queue.put(f"출력 콜백 상태: {status}")

            # 지터 버퍼 깊이 모니터링 (적응형 drop)
            current_depth = len(self.mic_jitter_buffer)
            target_depth = self._current_latency_params['jitter_depth']
            excess_threshold = target_depth + 2

            # 깊이가 (목표+2) 초과 상태인지 확인
            if current_depth > excess_threshold:
                self.jitter_depth_excess_count += 1
            else:
                self.jitter_depth_excess_count = 0

            # 연속 20회 초과 관측 시 한 블록 drop (정상 상태 수렴 강제)
            if self.jitter_depth_excess_count >= 20 and len(self.mic_jitter_buffer) > target_depth:
                self.mic_jitter_buffer.popleft()
                self.stats['jitter_adaptive_drop'] += 1
                self.jitter_depth_excess_count = 0

            # 지터 버퍼에서 마이크 블록 pop
            if len(self.mic_jitter_buffer) > 0:
                mic_output = self.mic_jitter_buffer.popleft()
                if mic_output.shape[0] == frames:
                    self._last_mic_block = mic_output
            else:
                # 버퍼 비면 직전 블록을 감쇠 재사용 (무음 갭 대신 — 끊김 은폐)
                last = getattr(self, '_last_mic_block', None)
                if last is not None and last.shape[0] == frames:
                    mic_output = last * 0.7
                    self._last_mic_block = mic_output
                else:
                    mic_output = np.zeros((frames, 2), dtype=np.float32)
                self.stats['mic_underrun'] += 1

            # MR 믹싱
            mix_output = mic_output.copy()

            if self.mr_buffer is not None and self.mr_playing and len(self.mr_buffer) > 0:
                # MR 버퍼에서 현재 위치의 블록 추출
                mr_block = self._get_mr_block(frames)
                if mr_block is not None:
                    mix_output += mr_block * self.mr_volume

            # 클리핑 방지: 블록 피크 리미터 (tanh 제거)
            peak = float(np.max(np.abs(mix_output)))
            if peak * self.limiter_gain > 0.98:
                # 피크가 한계를 초과 → 게인을 즉시 낮춤 (어택 빠름)
                self.limiter_gain = 0.98 / max(peak, 1e-9)
            else:
                # 정상 범위 → 게인을 천천히 회복 (릴리즈 완만)
                self.limiter_gain += (1.0 - self.limiter_gain) * 0.05

            mix_output = mix_output * self.limiter_gain
            np.clip(mix_output, -1.0, 1.0, out=mix_output)  # 안전망

            # 메인 출력에 기록
            outdata[:] = mix_output.astype(np.float32)

            # 모니터 버퍼에 블록 단위로 push (리미터 적용 후의 최종 믹스)
            self.monitor_buffer.append(mix_output.copy())

            # 녹음 큐에 최종 믹스 블록 추가 (녹음 중일 때만)
            if self.is_recording and not self.recording_queue.full():
                try:
                    self.recording_queue.put_nowait(mix_output.copy())
                except queue.Full:
                    # 큐가 가득 차면 스킵 (저장 스레드가 느린 경우)
                    self.stats['mic_overflow_drop'] += 1

        except Exception as e:
            self.error_queue.put(f"출력 콜백 예외: {str(e)}\n{traceback.format_exc()}")
            outdata.fill(0)  # 안전하게 무음 출력

    def _monitor_callback(self, outdata, frames, time, status):
        """모니터 출력 콜백 (블록 단위 처리)"""
        try:
            if status:
                self.error_queue.put(f"모니터 콜백 상태: {status}")

            output = np.zeros((frames, 2), dtype=np.float32)
            offset = 0

            # 보관 중인 부분 블록부터 채우기
            if len(self.monitor_partial_block) > 0:
                remaining = frames - offset
                copy_len = min(len(self.monitor_partial_block), remaining)
                output[offset:offset + copy_len] = self.monitor_partial_block[:copy_len] * self.monitor_volume
                offset += copy_len
                self.monitor_partial_block = self.monitor_partial_block[copy_len:]

            # 버퍼에서 블록 단위로 pop해서 채우기
            while offset < frames and len(self.monitor_buffer) > 0:
                block = self.monitor_buffer.popleft()  # (block_size, 2)
                remaining = frames - offset
                copy_len = min(len(block), remaining)
                output[offset:offset + copy_len] = block[:copy_len] * self.monitor_volume
                offset += copy_len

                # 남은 부분 보관
                if copy_len < len(block):
                    self.monitor_partial_block = block[copy_len:]

            # 비어있으면 나머지는 무음
            outdata[:] = output
        except Exception as e:
            self.error_queue.put(f"모니터 콜백 예외: {str(e)}")
            outdata.fill(0)

    def _get_mr_block(self, frames: int) -> Optional[np.ndarray]:
        """현재 위치에서 MR 블록 추출"""
        if self.mr_buffer is None or len(self.mr_buffer) == 0:
            return None

        end_pos = min(self.mr_position + frames, len(self.mr_buffer))
        block = self.mr_buffer[self.mr_position:end_pos].copy()

        # 부족한 부분은 0 패딩
        if len(block) < frames:
            pad = np.zeros((frames - len(block), 2), dtype=np.float32)
            block = np.vstack([block, pad])

        self.mr_position += frames

        # 곡 끝에 도달하면 재생 중지
        if self.mr_position >= len(self.mr_buffer):
            self.mr_playing = False

        return block.astype(np.float32)

    def load_mr(self, file_path: str):
        """
        MR 파일 로드 및 리샘플
        지원: mp3, wav, flac, ogg, m4a (pedalboard.io.AudioFile 사용)
        m4a 등 pedalboard에서 미지원하는 포맷은 ffmpeg로 임시 wav로 변환 후 로드
        """
        try:
            with AudioFile(file_path).resampled_to(self.sample_rate) as af:
                mr_data = af.read(af.frames)  # shape: (channels, frames)

            # (channels, frames) -> (frames, channels) 및 스테레오 변환
            mr_data = mr_data.T  # (frames, channels)

            if mr_data.shape[1] == 1:
                # 모노 → 스테레오 복제
                mr_data = np.tile(mr_data, (1, 2))
            elif mr_data.shape[1] > 2:
                # 5.1ch 등 → 처음 2개 채널만 사용
                mr_data = mr_data[:, :2]

            with self.lock:
                self.mr_buffer = mr_data.astype(np.float32)
                self.mr_position = 0
                self.mr_playing = False
                self.mr_original_path = file_path
                self.pitch_shift_semitones = 0  # 키 리셋
                self.tempo_stretch_factor = 1.0  # 템포 리셋

            return True
        except Exception as e:
            # pedalboard 로드 실패 시 ffmpeg 폴백 시도
            self.error_queue.put(f"직접 로드 실패, ffmpeg 폴백 시도 ({file_path}): {str(e)}")
            return self._load_mr_with_ffmpeg_fallback(file_path)

    def _load_mr_with_ffmpeg_fallback(self, file_path: str) -> bool:
        """
        ffmpeg로 임시 wav 파일로 변환한 후 AudioFile로 로드
        """
        temp_wav = None
        try:
            # 임시 wav 파일 생성
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                temp_wav = tmp.name

            # ffmpeg 명령어: 48kHz stereo wav로 변환
            cmd = [
                get_ffmpeg_path(),
                '-y',
                '-i', file_path,
                '-ar', str(self.sample_rate),
                '-ac', '2',
                temp_wav
            ]

            # Windows에서 콘솔 창 나타나지 않게 설정
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= 0x08000000  # CREATE_NO_WINDOW

            # ffmpeg 실행
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                startupinfo=startupinfo
            )

            if result.returncode != 0:
                raise Exception(f"ffmpeg 변환 실패: {result.stderr}")

            # 변환된 wav 파일 로드
            with AudioFile(temp_wav).resampled_to(self.sample_rate) as af:
                mr_data = af.read(af.frames)  # shape: (channels, frames)

            # (channels, frames) -> (frames, channels) 및 스테레오 변환
            mr_data = mr_data.T  # (frames, channels)

            if mr_data.shape[1] == 1:
                mr_data = np.tile(mr_data, (1, 2))
            elif mr_data.shape[1] > 2:
                mr_data = mr_data[:, :2]

            with self.lock:
                self.mr_buffer = mr_data.astype(np.float32)
                self.mr_position = 0
                self.mr_playing = False
                self.mr_original_path = file_path
                self.pitch_shift_semitones = 0
                self.tempo_stretch_factor = 1.0

            return True

        except Exception as e:
            self.error_queue.put(f"ffmpeg 폴백 로드 실패 ({file_path}): {str(e)}")
            return False
        finally:
            # 임시 파일 정리
            if temp_wav and os.path.exists(temp_wav):
                try:
                    os.remove(temp_wav)
                except:
                    pass

    def play(self):
        """재생 시작"""
        with self.lock:
            if self.mr_buffer is not None:
                self.mr_playing = True

    def pause(self):
        """재생 일시정지"""
        with self.lock:
            self.mr_playing = False

    def stop(self):
        """재생 중지 및 위치 초기화"""
        with self.lock:
            self.mr_playing = False
            self.mr_position = 0

    def seek(self, seconds: float):
        """재생 위치 이동 (초 단위)"""
        with self.lock:
            if self.mr_buffer is not None:
                frame_pos = int(seconds * self.sample_rate)
                self.mr_position = max(0, min(frame_pos, len(self.mr_buffer) - 1))

    def get_current_time(self) -> float:
        """현재 재생 시간(초)"""
        with self.lock:
            if self.mr_buffer is None:
                return 0.0
            return self.mr_position / self.sample_rate

    def get_duration(self) -> float:
        """곡 길이(초)"""
        with self.lock:
            if self.mr_buffer is None:
                return 0.0
            return len(self.mr_buffer) / self.sample_rate

    def set_mr_volume(self, volume: float):
        """MR 볼륨 설정 (0~1.5)"""
        with self.lock:
            self.mr_volume = max(0.0, min(1.5, volume))

    def set_mic_volume(self, volume: float):
        """마이크 볼륨 설정 (0~1.5)"""
        with self.lock:
            self.mic_volume = max(0.0, min(1.5, volume))

    def set_monitor_volume(self, volume: float):
        """모니터 볼륨 설정 (0~1.5)"""
        with self.lock:
            self.monitor_volume = max(0.0, min(1.5, volume))

    def set_latency_mode(self, mode: str) -> bool:
        """
        지연 모드 설정 및 스트림 재시작
        mode: 'low' / 'balanced' / 'stable'
        """
        if mode not in self.latency_mode_params:
            return False

        self.latency_mode = mode
        self._current_latency_params = self.latency_mode_params[mode]

        # 스트림을 재시작해야 blocksize 변경이 적용됨
        # app.py에서 현재 장치 설정을 읽어 재시작하도록 요청
        return True

    def get_latency_mode(self) -> str:
        """현재 지연 모드"""
        return self.latency_mode

    def get_estimated_latency_ms(self) -> float:
        """
        예상 지연(ms) 계산
        = input_stream.latency + output_stream.latency + (지터 깊이 × blocksize / 48000)
        """
        try:
            latency = 0.0

            # 입출력 스트림 지연 (InputStream/OutputStream의 latency는 float — duplex Stream만 튜플)
            if self.input_stream is not None:
                latency += float(self.input_stream.latency) * 1000

            if self.output_stream is not None:
                latency += float(self.output_stream.latency) * 1000

            # 지터 버퍼 지연
            current_jitter_depth = len(self.mic_jitter_buffer)
            jitter_latency_ms = (current_jitter_depth * self.block_size / self.sample_rate) * 1000
            latency += jitter_latency_ms

            return latency
        except:
            return 0.0

    def set_agc_enabled(self, enabled: bool):
        """AGC(자동 음량 조절) 활성화/비활성화"""
        with self.lock:
            self.agc_enabled = enabled

    def get_agc_enabled(self) -> bool:
        """AGC 활성화 여부 조회"""
        with self.lock:
            return self.agc_enabled

    def get_agc_gain(self) -> float:
        """현재 AGC 배율 조회"""
        with self.lock:
            return self.agc_gain

    def set_voice_correction(self, enabled: bool, strength: str = "medium") -> bool:
        """
        목소리 보정 설정
        Args:
            enabled: 보정 활성화 여부
            strength: "weak" / "medium" / "strong"
        Returns:
            성공 여부
        """
        if strength not in ["weak", "medium", "strong"]:
            return False

        # 보정 파라미터 정의
        correction_params = {
            "weak": {
                "gate_threshold": -60,
                "hpf_cutoff": 80,
                "comp_threshold": -16,
                "comp_ratio": 2,
                "comp_attack": 5,
                "comp_release": 120,
                "peak_freq": 3500,
                "peak_gain": 1.5,
                "peak_q": 0.9,
                "lshelf_freq": 160,
                "lshelf_gain": 0.0,  # 약모드는 저음 부스트 없음
                "makeup_gain": 2,
            },
            "medium": {
                "gate_threshold": -55,
                "hpf_cutoff": 85,
                "comp_threshold": -18,
                "comp_ratio": 3,
                "comp_attack": 5,
                "comp_release": 100,
                "peak_freq": 3500,
                "peak_gain": 2.5,
                "peak_q": 0.9,
                "lshelf_freq": 160,
                "lshelf_gain": 0.0,  # 중음도 저음 부스트 없음
                "makeup_gain": 3,
            },
            "strong": {
                "gate_threshold": -50,
                "hpf_cutoff": 95,
                "comp_threshold": -22,
                "comp_ratio": 4,
                "comp_attack": 3,
                "comp_release": 80,
                "peak_freq": 3500,
                "peak_gain": 3.5,
                "peak_q": 1.0,
                "lshelf_freq": 160,
                "lshelf_gain": 1.5,  # 강모드만 저음 부스트
                "makeup_gain": 4,
            }
        }

        with self.lock:
            self.voice_correction_enabled = enabled
            self.voice_correction_strength = strength

            if enabled:
                params = correction_params[strength]
                new_chain = []

                try:
                    # 1. NoiseGate
                    new_chain.append(pb.NoiseGate(threshold_db=params["gate_threshold"]))

                    # 2. HighpassFilter (저음 노이즈 제거)
                    new_chain.append(pb.HighpassFilter(cutoff_frequency_hz=params["hpf_cutoff"]))

                    # 3. Compressor (음량 고르게)
                    new_chain.append(pb.Compressor(
                        threshold_db=params["comp_threshold"],
                        ratio=params["comp_ratio"],
                        attack_ms=params["comp_attack"],
                        release_ms=params["comp_release"]
                    ))

                    # 4. PeakFilter (프레즌스 부스트) - 이름이 다를 수 있으니 시도
                    try:
                        new_chain.append(pb.PeakFilter(
                            cutoff_frequency_hz=params["peak_freq"],
                            gain_db=params["peak_gain"],
                            q=params["peak_q"]
                        ))
                    except AttributeError:
                        # PeakFilter가 없으면 Parametric 시도
                        try:
                            new_chain.append(pb.Parametric(
                                center_frequency_hz=params["peak_freq"],
                                gain_db=params["peak_gain"],
                                q=params["peak_q"]
                            ))
                        except AttributeError:
                            # 둘 다 없으면 LowShelfFilter로 대체 (프레즌스 부스트 스킵)
                            pass

                    # 5. LowShelfFilter (저음 부스트 - 강모드만)
                    if params["lshelf_gain"] > 0:
                        try:
                            new_chain.append(pb.LowShelfFilter(
                                cutoff_frequency_hz=params["lshelf_freq"],
                                gain_db=params["lshelf_gain"]
                            ))
                        except AttributeError:
                            pass

                    # 6. Gain (컴프레서 손실 보상)
                    new_chain.append(pb.Gain(gain_db=float(params["makeup_gain"])))

                    # 원자적 교체: 콜백이 리스트를 순회하는 도중에 in-place 수정되지 않도록
                    self.voice_correction_chain = new_chain
                    return True

                except Exception as e:
                    self.error_queue.put(f"목소리 보정 체인 구성 실패: {str(e)}")
                    self.voice_correction_chain = []
                    return False
            else:
                # 보정 비활성화: 체인 비우기
                self.voice_correction_chain = []
                return True

    def get_voice_correction_status(self) -> Dict[str, any]:
        """목소리 보정 상태 조회"""
        with self.lock:
            return {
                "enabled": self.voice_correction_enabled,
                "strength": self.voice_correction_strength,
                "chain_length": len(self.voice_correction_chain)
            }

    def set_echo_preset(self, preset_name: str):
        """에코 프리셋 적용"""
        with self.lock:
            presets = {
                "끄기": {"delay": 0.05, "feedback": 0.0, "mix": 0.0, "room": 0.0, "wet": 0.0, "dry": 0.75},
                "노래방": {"delay": 0.25, "feedback": 0.45, "mix": 0.32, "room": 0.55, "wet": 0.40, "dry": 0.80},
                "콘서트홀": {"delay": 0.30, "feedback": 0.50, "mix": 0.35, "room": 0.75, "wet": 0.50, "dry": 0.75},
                "동굴": {"delay": 0.40, "feedback": 0.60, "mix": 0.45, "room": 0.90, "wet": 0.55, "dry": 0.70},
            }
            if preset_name in presets:
                p = presets[preset_name]
                self.delay_effect.delay_seconds = p["delay"]
                self.delay_effect.feedback = p["feedback"]
                self.delay_effect.mix = p["mix"]
                self.reverb_effect.room_size = p["room"]
                self.reverb_effect.wet_level = p["wet"]
                self.reverb_effect.dry_level = p["dry"]

    def set_delay_effect(self, delay_seconds: float, feedback: float, mix: float):
        """에코 이펙트 설정"""
        with self.lock:
            self.delay_effect.delay_seconds = max(0.05, min(0.6, delay_seconds))
            self.delay_effect.feedback = max(0.0, min(0.7, feedback))
            self.delay_effect.mix = max(0.0, min(1.0, mix))

    def set_reverb_effect(self, room_size: float, wet_level: float, dry_level: float):
        """리버브 이펙트 설정"""
        with self.lock:
            self.reverb_effect.room_size = max(0.0, min(1.0, room_size))
            self.reverb_effect.wet_level = max(0.0, min(1.0, wet_level))
            self.reverb_effect.dry_level = max(0.0, min(1.0, dry_level))

    def shift_pitch(self, semitones: int):
        """
        키 조절 (±6 반음)
        백그라운드 스레드에서 키+템포 동시 렌더링 수행
        """
        semitones = max(-6, min(6, semitones))

        if semitones == self.pitch_shift_semitones or self.mr_buffer is None:
            return

        # 이미 렌더 중이면 새 요청만 등록 (마지막 요청만 반영)
        if self.is_pitch_shifting and self.pitch_shift_thread and self.pitch_shift_thread.is_alive():
            self.pitch_render_generation += 1
            return

        # 백그라운드 렌더링 시작
        self.pitch_render_generation += 1
        current_gen = self.pitch_render_generation
        self.is_pitch_shifting = True
        self.pitch_shift_thread = threading.Thread(
            target=self._tempo_render_async,
            args=(semitones, self.tempo_stretch_factor, current_gen),
            daemon=True
        )
        self.pitch_shift_thread.start()

    def set_tempo(self, stretch_factor: float):
        """
        템포 조절 (0.5~1.5)
        백그라운드 스레드에서 키+템포 동시 렌더링 수행
        """
        stretch_factor = max(0.5, min(1.5, stretch_factor))

        if stretch_factor == self.tempo_stretch_factor or self.mr_buffer is None:
            return

        # 이미 렌더 중이면 새 요청만 등록 (마지막 요청만 반영)
        if self.is_tempo_stretching and self.tempo_stretch_thread and self.tempo_stretch_thread.is_alive():
            self.tempo_render_generation += 1
            return

        # 백그라운드 렌더링 시작
        self.tempo_render_generation += 1
        current_gen = self.tempo_render_generation
        self.is_tempo_stretching = True
        self.tempo_stretch_thread = threading.Thread(
            target=self._tempo_render_async,
            args=(self.pitch_shift_semitones, stretch_factor, current_gen),
            daemon=True
        )
        self.tempo_stretch_thread.start()

    def _tempo_render_async(self, semitones: int, stretch_factor: float, generation: int):
        """비동기 키+템포 동시 렌더링"""
        try:
            with self.lock:
                # 이 요청이 stale인지 확인
                if generation != self.pitch_render_generation and generation != self.tempo_render_generation:
                    return

                if self.mr_original_path is None or self.mr_buffer is None:
                    return

                # 원본 MR 재로드 (오프셋 누적 방지)
                with AudioFile(self.mr_original_path).resampled_to(self.sample_rate) as af:
                    mr_data = af.read(af.frames)  # (channels, frames)

                mr_data = mr_data.T  # (frames, channels)
                if mr_data.shape[1] == 1:
                    mr_data = np.tile(mr_data, (1, 2))
                elif mr_data.shape[1] > 2:
                    mr_data = mr_data[:, :2]

                mr_data = mr_data.astype(np.float32)

                # 키+템포 동시 렌더 (time_stretch 사용)
                # pedalboard.time_stretch는 (channels, frames) 입력을 기대함
                if semitones != 0 or stretch_factor != 1.0:
                    mr_data_t = mr_data.T  # (channels, frames)
                    mr_data_t = pb.time_stretch(
                        mr_data_t,
                        self.sample_rate,
                        stretch_factor=stretch_factor,
                        pitch_shift_in_semitones=semitones
                    )
                    mr_data = mr_data_t.T  # (frames, channels)

                # 현재 재생 위치 비율 유지
                old_buffer_len = len(self.mr_buffer)
                if self.mr_position > 0 and old_buffer_len > 0:
                    old_ratio = self.mr_position / old_buffer_len
                    self.mr_position = int(old_ratio * len(mr_data))

                self.mr_buffer = mr_data
                self.pitch_shift_semitones = semitones
                self.tempo_stretch_factor = stretch_factor

        except Exception as e:
            self.error_queue.put(f"키+템포 렌더링 실패: {str(e)}")

        finally:
            self.is_pitch_shifting = False
            self.is_tempo_stretching = False

    def get_pitch_shift_semitones(self) -> int:
        """현재 키 오프셋(반음)"""
        with self.lock:
            return self.pitch_shift_semitones

    def is_pitch_shifting_in_progress(self) -> bool:
        """키 조절 렌더링 진행 중 여부"""
        return self.is_pitch_shifting

    def is_tempo_rendering_in_progress(self) -> bool:
        """템포 조절 렌더링 진행 중 여부"""
        return self.is_tempo_stretching

    def get_tempo_stretch_factor(self) -> float:
        """현재 템포 배율"""
        with self.lock:
            return self.tempo_stretch_factor

    def get_original_position_seconds(self) -> float:
        """
        원본 영상 기준 재생 위치 (초)

        현재 렌더 버퍼의 재생 위치를 템포 배율로 보정하여 반환.
        템포가 1.25배이고 렌더 버퍼가 10초 진행했다면 → 원본 12.5초 반환
        (키 변경은 길이에 영향 없으므로 템포 배율만 고려)
        """
        with self.lock:
            if self.mr_buffer is None:
                return 0.0
            render_position_sec = self.mr_position / self.sample_rate
            # stretch_factor 1.25 = 렌더 길이가 원본의 1/1.25로 짧아짐
            # → 렌더 t초 지점은 원본 기준 t × 템포 지점
            original_position_sec = render_position_sec * self.tempo_stretch_factor
            return original_position_sec

    def reset_pitch_and_tempo(self):
        """키+템포 리셋"""
        with self.lock:
            self.pitch_shift_semitones = 0
            self.tempo_stretch_factor = 1.0

        # 원본 버퍼 재로드
        if self.mr_original_path and self.mr_buffer is not None:
            try:
                with AudioFile(self.mr_original_path).resampled_to(self.sample_rate) as af:
                    mr_data = af.read(af.frames)  # (channels, frames)

                mr_data = mr_data.T  # (frames, channels)
                if mr_data.shape[1] == 1:
                    mr_data = np.tile(mr_data, (1, 2))
                elif mr_data.shape[1] > 2:
                    mr_data = mr_data[:, :2]

                with self.lock:
                    self.mr_buffer = mr_data.astype(np.float32)
                    # 재생 위치 비율 유지
                    if self.mr_position > 0:
                        old_ratio = self.mr_position / len(mr_data)
                        self.mr_position = int(old_ratio * len(mr_data))
            except Exception as e:
                self.error_queue.put(f"키+템포 리셋 실패: {str(e)}")

    def download_youtube_mr(self, url: str, output_folder: str, progress_callback=None) -> Optional[str]:
        """
        유튜브 영상에서 mp4 영상 + mp3 오디오 다운로드
        같은 basename으로 .mp4(영상)와 .mp3(재생용) 파일이 남음

        Args:
            url: 유튜브 URL
            output_folder: 저장 폴더 경로
            progress_callback: 진행 콜백 함수 (info_dict 수신)
        Returns:
            다운로드한 mp3 파일 경로, 실패 시 None
        """
        try:
            import yt_dlp
        except ImportError:
            self.error_queue.put("yt-dlp 미설치: pip install yt-dlp 실행")
            return None

        try:
            import re
            from pathlib import Path as PathlibPath

            # 저장 폴더 생성
            output_path = PathlibPath(output_folder)
            output_path.mkdir(parents=True, exist_ok=True)

            # Windows 금지 문자 치환 함수
            def sanitize_filename(name: str) -> str:
                invalid_chars = r'[\/:*?"<>|]'
                return re.sub(invalid_chars, '_', name)

            # yt-dlp 옵션: mp4 영상 + FFmpegExtractAudio로 mp3 추출
            ydl_opts = {
                'format': 'bv*[height<=720][ext=mp4]+ba/b[height<=720]/b',
                'merge_output_format': 'mp4',
                'keepvideo': True,  # mp3 추출 후에도 mp4 유지
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl': str(output_path / '%(title)s.%(ext)s'),
                'noplaylist': True,
                'quiet': False,
                'no_warnings': False,
            }

            # frozen 실행 시에만 ffmpeg_location 설정 (PATH의 ffmpeg일 때는 키 생략)
            if getattr(sys, 'frozen', False):
                ffmpeg_path = get_ffmpeg_path()
                if ffmpeg_path != 'ffmpeg':  # 포터블 ffmpeg.exe를 찾은 경우
                    ydl_opts['ffmpeg_location'] = ffmpeg_path

            # 진행 콜백 등록
            if progress_callback:
                ydl_opts['progress_hooks'] = [progress_callback]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # yt-dlp가 실제로 저장한 파일명 기준으로 경로 계산
                # (yt-dlp는 특수문자를 자체 규칙으로 치환하므로 제목 기반 추측은 어긋날 수 있음)
                base = os.path.splitext(ydl.prepare_filename(info))[0]
                mp3_filename = base + '.mp3'

                if os.path.exists(mp3_filename):
                    return mp3_filename

                # 이름이 어긋난 경우: 폴더에서 가장 최근 mp3로 폴백
                candidates = sorted(output_path.glob('*.mp3'),
                                    key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    return str(candidates[0])

                self.error_queue.put(f"mp3 파일 생성 실패: {mp3_filename}")
                return None

        except Exception as e:
            self.error_queue.put(f"유튜브 다운로드 실패: {str(e)}")
            return None

    def get_stats(self) -> Dict:
        """통계 조회 (마이크 언더런/오버플로우/xrun 카운트)"""
        with self.lock:
            return self.stats.copy()

    def check_device_samplerates(self, mic_idx: int, out_idx: int) -> List[Dict]:
        """
        장치 샘플레이트 불일치 감지
        Returns: [{'device_name': str, 'index': int, 'default_samplerate': int}, ...]
        48000과 다른 장치 목록 반환
        """
        try:
            mismatched = []
            devices = sd.query_devices()

            for idx in [mic_idx, out_idx]:
                if 0 <= idx < len(devices):
                    # 실제로 스트림이 열리는 장치(WASAPI 해석 결과)를 검사해야 함.
                    # MME 항목은 Windows 형식 설정과 무관하게 항상 44100으로 보고되므로
                    # MME 인덱스를 그대로 검사하면 영구 오탐이 된다.
                    resolved_idx, _ = self._resolve_to_wasapi(idx)
                    dev = devices[resolved_idx]
                    default_sr = dev.get('default_samplerate', 48000)
                    if default_sr != 48000:
                        mismatched.append({
                            'device_name': dev['name'],
                            'index': resolved_idx,
                            'default_samplerate': int(default_sr)
                        })
            return mismatched
        except Exception as e:
            self.error_queue.put(f"샘플레이트 불일치 검사 실패: {str(e)}")
            return []

    def start_recording(self, filepath: str) -> bool:
        """
        녹음 시작
        Args:
            filepath: 저장할 WAV 파일 경로
        Returns:
            성공 여부
        """
        if self.is_recording:
            self.error_queue.put("녹음이 이미 진행 중입니다")
            return False

        # 스트림이 동작 중인지 확인
        if self.input_stream is None or not self.input_stream.active:
            self.error_queue.put("스트림이 동작 중이 아닙니다")
            return False

        try:
            self.is_recording = True
            self.recording_filepath = filepath
            self.recording_start_time = time.time()
            self.recording_stop_event.clear()

            # 라이터 스레드 시작
            self.recording_thread = threading.Thread(
                target=self._recording_writer_thread,
                daemon=True
            )
            self.recording_thread.start()

            return True
        except Exception as e:
            self.error_queue.put(f"녹음 시작 실패: {str(e)}")
            self.is_recording = False
            return False

    def stop_recording(self) -> float:
        """
        녹음 중지 및 파일 정리
        Returns:
            녹음 시간(초), 실패 시 0.0
        """
        if not self.is_recording:
            return 0.0

        try:
            self.is_recording = False
            self.recording_stop_event.set()

            # 라이터 스레드가 종료될 때까지 대기 (최대 5초)
            if self.recording_thread and self.recording_thread.is_alive():
                self.recording_thread.join(timeout=5.0)

            # 녹음 시간 계산
            if self.recording_start_time is not None:
                recording_duration = time.time() - self.recording_start_time
                return max(0.0, recording_duration)

            return 0.0

        except Exception as e:
            self.error_queue.put(f"녹음 중지 실패: {str(e)}")
            return 0.0
        finally:
            self.recording_filepath = None
            self.recording_start_time = None

    def get_recording_seconds(self) -> float:
        """현재 녹음 경과 시간(초)"""
        if not self.is_recording or self.recording_start_time is None:
            return 0.0
        return time.time() - self.recording_start_time

    def _recording_writer_thread(self):
        """
        별도 스레드: 녹음 큐를 드레인해 WAV 파일로 기록
        """
        if not self.recording_filepath:
            return

        wav_file = None
        try:
            # WAV 파일 생성 (48kHz, 스테레오, 16bit PCM)
            wav_file = wave.open(self.recording_filepath, 'wb')
            wav_file.setnchannels(2)  # 스테레오
            wav_file.setsampwidth(2)  # 16bit = 2바이트
            wav_file.setframerate(self.sample_rate)  # 48000Hz

            # 녹음 중 또는 큐에 데이터가 남아있는 동안
            while not self.recording_stop_event.is_set() or not self.recording_queue.empty():
                try:
                    # 큐에서 블록 추출 (타임아웃: 100ms)
                    block = self.recording_queue.get(timeout=0.1)

                    # float32 (-1~1) → int16 (-32767~32767) 변환
                    audio_int16 = np.clip(block, -1.0, 1.0) * 32767
                    audio_int16 = audio_int16.astype(np.int16)

                    # WAV에 기록
                    wav_file.writeframes(audio_int16.tobytes())

                except queue.Empty:
                    # 큐가 비어있으면 계속 대기
                    continue

        except Exception as e:
            self.error_queue.put(f"녹음 파일 쓰기 실패: {str(e)}\n{traceback.format_exc()}")

        finally:
            # WAV 파일 닫기
            if wav_file:
                try:
                    wav_file.close()
                except:
                    pass

    def shutdown(self):
        """엔진 종료"""
        # 녹음 중이면 정지
        if self.is_recording:
            self.stop_recording()

        self._stop_stream()
        while not self.error_queue.empty():
            try:
                self.error_queue.get_nowait()
            except queue.Empty:
                break

    def __del__(self):
        self.shutdown()
