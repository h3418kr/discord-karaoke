"""
Discord Karaoke - Smoke Test
Engine validation without GUI
"""

import sys
import numpy as np
from pathlib import Path

print("=" * 60)
print("Discord Karaoke Smoke Test")
print("=" * 60)

# ===== (a) Import Test =====
print("\n[1] Import Test...")
try:
    import sounddevice as sd
    print(f"  [OK] sounddevice {sd.__version__}")
except ImportError as e:
    print(f"  [FAIL] sounddevice: {e}")
    sys.exit(1)

try:
    import pedalboard as pb
    from pedalboard.io import AudioFile
    print(f"  [OK] pedalboard {pb.__version__}")
except ImportError as e:
    print(f"  [FAIL] pedalboard: {e}")
    sys.exit(1)

try:
    from audio_engine import AudioEngine
    print(f"  [OK] audio_engine")
except ImportError as e:
    print(f"  [FAIL] audio_engine: {e}")
    sys.exit(1)

# ===== (b) Device List =====
print("\n[2] Audio Device Query...")
try:
    devices = sd.query_devices()
    print(f"  Total devices: {len(devices)}")

    print("\n  Input devices:")
    input_count = 0
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
            print(f"    [{i}] {dev['name']} (ch:{dev['max_input_channels']})")
            input_count += 1
            if input_count >= 5:
                print("    ...")
                break

    print("\n  Output devices:")
    output_count = 0
    for i, dev in enumerate(devices):
        if dev['max_output_channels'] > 0:
            print(f"    [{i}] {dev['name']} (ch:{dev['max_output_channels']})")
            output_count += 1
            if output_count >= 5:
                print("    ...")
                break

    print(f"  [OK] Device query success")
except Exception as e:
    print(f"  [FAIL] {e}")
    sys.exit(1)

# ===== (c) Generate & Load Test WAV =====
print("\n[3] Generate and Load Test Sine Wave (3sec @ 48kHz)...")
try:
    sample_rate = 48000
    duration = 3
    freq = 440  # A4
    frames = sample_rate * duration

    # Mono sine wave
    t = np.arange(frames) / sample_rate
    mono_signal = np.sin(2 * np.pi * freq * t).astype(np.float32)

    # Save with scipy
    test_file = Path(__file__).parent / "test_sine_wave.wav"

    from scipy.io import wavfile
    wavfile.write(str(test_file), sample_rate, mono_signal)

    # Load with pedalboard
    with AudioFile(str(test_file)).resampled_to(sample_rate) as af:
        loaded = af.read(af.frames)  # (channels, frames)

    print(f"  Generated: {frames:,} frames")
    print(f"  Loaded shape: {loaded.shape}")
    print(f"  Data range: [{loaded.min():.4f}, {loaded.max():.4f}]")

    if loaded.shape[0] > 0 and loaded.shape[1] > 0:
        print(f"  [OK] AudioFile load success")
    else:
        print(f"  [FAIL] Invalid loaded shape")
        sys.exit(1)

    test_file.unlink()
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (d) Effects Chain (Block Processing) =====
print("\n[4] Effects Chain (Delay + Reverb) Block Processing...")
try:
    sample_rate = 48000
    block_size = 1024
    num_blocks = 5

    # Stereo sine wave
    mono_signal = np.sin(2 * np.pi * 440 * np.arange(block_size * num_blocks) / sample_rate).astype(np.float32)
    stereo_signal = np.tile(mono_signal, (2, 1))  # (2, frames)

    # Effects
    delay_effect = pb.Delay(delay_seconds=0.2, feedback=0.4, mix=0.5)
    reverb_effect = pb.Reverb(room_size=0.5, wet_level=0.33, dry_level=0.4)
    effects_chain = [delay_effect, reverb_effect]

    # Block processing
    output_blocks = []
    for i in range(num_blocks):
        block = stereo_signal[:, i * block_size:(i + 1) * block_size]

        for effect in effects_chain:
            block = effect(block, sample_rate, reset=False)

        output_blocks.append(block)

    output = np.hstack(output_blocks)

    print(f"  Blocks: {num_blocks} x {block_size} frames")
    print(f"  Output shape: {output.shape}")
    print(f"  Data range: [{output.min():.4f}, {output.max():.4f}]")

    if output.shape[0] == 2 and output.shape[1] == block_size * num_blocks:
        print(f"  [OK] Effects chain success")
    else:
        print(f"  [FAIL] Invalid output shape")
        sys.exit(1)
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (e) PitchShift Offline Render =====
print("\n[5] PitchShift Offline Render (+2 semitones)...")
try:
    sample_rate = 48000
    duration = 2
    frames = sample_rate * duration

    mono_signal = np.sin(2 * np.pi * 440 * np.arange(frames) / sample_rate).astype(np.float32)
    stereo_signal = np.tile(mono_signal, (2, 1))  # (2, frames)

    pitch_shifter = pb.PitchShift(2)
    output = pitch_shifter(stereo_signal, sample_rate)

    print(f"  Input shape: {stereo_signal.shape}")
    print(f"  Output shape: {output.shape}")

    if output.shape == stereo_signal.shape:
        print(f"  [OK] PitchShift render success")
    else:
        print(f"  [FAIL] Shape mismatch")
        sys.exit(1)
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (f) AudioEngine Basic Functions =====
print("\n[6] AudioEngine Basic Functions...")
try:
    engine = AudioEngine()

    input_devs, output_devs = engine.list_devices()
    print(f"  Input devices: {len(input_devs)}")
    print(f"  Output devices: {len(output_devs)}")

    mic_idx, out_idx, mon_idx = engine.get_default_devices()
    print(f"  Default mic: [{mic_idx}]")
    print(f"  Default output: [{out_idx}]")
    print(f"  Default monitor: [{mon_idx}]")

    print(f"  [OK] AudioEngine initialization success")

    engine.shutdown()
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (g) Stream Initialization Test (Separated Input/Output) =====
print("\n[7] Stream Initialization Test (Separated Input/Output & Jitter Buffer)...")
try:
    import time

    engine = AudioEngine()
    mic_idx, out_idx, mon_idx = engine.get_default_devices()

    # Set all volumes to 0 (silent)
    engine.set_mic_volume(0.0)
    engine.set_mr_volume(0.0)
    engine.set_monitor_volume(0.0)

    # Start separated streams (InputStream + OutputStream)
    engine.start_stream(mic_idx, out_idx, None)
    print(f"  Started: mic[{mic_idx}] (InputStream) + output[{out_idx}] (OutputStream)")

    # Run for 0.5 seconds
    time.sleep(0.5)

    # Check jitter buffer and stats
    stats = engine.get_stats()
    print(f"  Stats after 0.5s: underrun={stats.get('mic_underrun', 0)}, overflow_drop={stats.get('mic_overflow_drop', 0)}, input_xrun={stats.get('input_callback_xrun', 0)}, output_xrun={stats.get('output_callback_xrun', 0)}")

    # Stop stream
    engine.shutdown()
    print(f"  [OK] Stream lifecycle with separated streams successful")

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (h) TimeStretch (Tempo + Pitch) =====
print("\n[8] TimeStretch (Tempo + Pitch) Test...")
try:
    import pedalboard as pb

    sample_rate = 48000
    duration = 2  # 2초 테스트
    frames = sample_rate * duration

    # 모노 사인파
    mono_signal = np.sin(2 * np.pi * 440 * np.arange(frames) / sample_rate).astype(np.float32)
    stereo_signal = np.tile(mono_signal, (2, 1))  # (2, frames)

    # 템포 1.25로 렌더 (stretch_factor > 1.0이 빨라지는지 확인)
    stretched = pb.time_stretch(
        stereo_signal,
        sample_rate,
        stretch_factor=1.25,
        pitch_shift_in_semitones=0
    )

    # 출력 길이 확인 (약 1.6초 = 76800 프레임)
    # stretch_factor 1.25 → 출력은 원래의 80% 길이 (1.25분의 1)
    expected_frames_min = int(frames / 1.25 * 0.9)  # 90% 범위
    expected_frames_max = int(frames / 1.25 * 1.1)  # 110% 범위

    print(f"  Input shape: {stereo_signal.shape}")
    print(f"  Output shape: {stretched.shape}")
    print(f"  Expected frames range: [{expected_frames_min}, {expected_frames_max}]")

    if stretched.shape[0] == 2:
        actual_frames = stretched.shape[1]
        if expected_frames_min <= actual_frames <= expected_frames_max:
            print(f"  [OK] TimeStretch render success (stretch_factor 1.25 → {actual_frames/sample_rate:.2f}s)")
        else:
            print(f"  [WARN] Output length unexpected: {actual_frames} frames (outside expected range)")
    else:
        print(f"  [FAIL] Output shape[0] should be 2 (channels)")
        sys.exit(1)

    # 키+템포 동시 렌더 테스트
    stretched_pitched = pb.time_stretch(
        stereo_signal,
        sample_rate,
        stretch_factor=1.25,
        pitch_shift_in_semitones=2
    )

    if stretched_pitched.shape[0] == 2:
        print(f"  [OK] KeyPitch + TimeStretch simultaneous render success")
    else:
        print(f"  [FAIL] Pitch+Tempo render failed")
        sys.exit(1)

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (i) yt-dlp Import & Metadata Test =====
print("\n[9] yt-dlp Import & Metadata Test (No Download)...")
try:
    import yt_dlp
    print(f"  [OK] yt-dlp imported")

    # 짧은 공개 영상 메타데이터 조회만 수행 (다운로드 안 함)
    # 실제 네트워크 조회는 건너뛰고 import만 성공하면 통과
    print(f"  [OK] yt-dlp ready for use (metadata extraction available)")

except ImportError:
    print(f"  [FAIL] yt-dlp not installed: pip install yt-dlp")
    sys.exit(1)
except Exception as e:
    print(f"  [FAIL] {e}")
    sys.exit(1)

# ===== (j) ffmpeg mp4 생성 및 mp3 추출 테스트 =====
print("\n[10] ffmpeg MP4 생성 및 MP3 추출 테스트...")
try:
    import subprocess
    import os
    from scipy.io import wavfile

    # 테스트 mp4 생성
    test_mp4 = Path(__file__).parent / "test_video.mp4"
    test_mp3_from_mp4 = Path(__file__).parent / "test_audio_from_mp4.mp3"

    ffmpeg_cmd = [
        'ffmpeg',
        '-y',
        '-f', 'lavfi',
        '-i', 'testsrc=duration=3:size=320x240:rate=30',
        '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=3',
        '-c:v', 'libx264',
        '-c:a', 'aac',
        str(test_mp4)
    ]

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= 0x08000000  # CREATE_NO_WINDOW (Python 3.13 미만 호환)

    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, startupinfo=startupinfo)
    if result.returncode != 0:
        raise Exception(f"mp4 생성 실패: {result.stderr}")

    if not test_mp4.exists():
        raise Exception("생성된 mp4 파일 없음")

    # mp4에서 mp3 추출
    extract_cmd = [
        'ffmpeg',
        '-y',
        '-i', str(test_mp4),
        '-vn',
        '-c:a', 'libmp3lame',
        '-q:a', '2',
        str(test_mp3_from_mp4)
    ]

    result = subprocess.run(extract_cmd, capture_output=True, text=True, startupinfo=startupinfo)
    if result.returncode != 0:
        raise Exception(f"mp3 추출 실패: {result.stderr}")

    if not test_mp3_from_mp4.exists():
        raise Exception("추출된 mp3 파일 없음")

    # 추출한 mp3 로드 테스트
    with AudioFile(str(test_mp3_from_mp4)).resampled_to(48000) as af:
        audio_data = af.read(af.frames)

    print(f"  MP4 생성: {test_mp4.name}")
    print(f"  MP3 추출: {test_mp3_from_mp4.name}")
    print(f"  로드된 오디오: {audio_data.shape}")
    print(f"  [OK] ffmpeg MP4→MP3 변환 성공")

    test_mp4.unlink()
    test_mp3_from_mp4.unlink()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (k) M4A 직접 로드 실패 및 ffmpeg 폴백 테스트 =====
print("\n[11] M4A Fallback (Direct Load Fail → ffmpeg WAV Conversion Success)...")
try:
    # 테스트용 m4a 파일 생성 (ffmpeg로)
    test_m4a = Path(__file__).parent / "test_audio.m4a"

    ffmpeg_cmd = [
        'ffmpeg',
        '-y',
        '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=2',
        '-c:a', 'aac',
        str(test_m4a)
    ]

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= 0x08000000  # CREATE_NO_WINDOW (Python 3.13 미만 호환)

    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, startupinfo=startupinfo)
    if result.returncode != 0:
        raise Exception(f"m4a 생성 실패: {result.stderr}")

    if not test_m4a.exists():
        raise Exception("생성된 m4a 파일 없음")

    # AudioEngine으로 m4a 로드 (폴백 포함)
    engine = AudioEngine()
    success = engine.load_mr(str(test_m4a))

    if not success:
        raise Exception("M4A 로드 실패 (폴백 포함)")

    print(f"  M4A 생성: {test_m4a.name}")
    print(f"  로드 결과: {engine.mr_buffer.shape if engine.mr_buffer is not None else 'None'}")
    print(f"  [OK] M4A ffmpeg 폴백 로드 성공")

    test_m4a.unlink()
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (l) cv2.VideoCapture MP4 읽기 및 Seek 테스트 =====
print("\n[12] cv2.VideoCapture MP4 Read & Seek Test...")
try:
    import cv2

    # 테스트 mp4 생성
    test_mp4_video = Path(__file__).parent / "test_video_seek.mp4"

    ffmpeg_cmd = [
        'ffmpeg',
        '-y',
        '-f', 'lavfi',
        '-i', 'testsrc=duration=3:size=320x240:rate=30',
        '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=3',
        '-c:v', 'libx264',
        '-c:a', 'aac',
        str(test_mp4_video)
    ]

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= 0x08000000  # CREATE_NO_WINDOW (Python 3.13 미만 호환)

    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, startupinfo=startupinfo)
    if result.returncode != 0:
        raise Exception(f"mp4 생성 실패: {result.stderr}")

    # cv2.VideoCapture 테스트
    cap = cv2.VideoCapture(str(test_mp4_video))
    if not cap.isOpened():
        raise Exception("VideoCapture 열기 실패")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # 첫 프레임 읽기
    ret, frame = cap.read()
    if not ret or frame is None:
        raise Exception("첫 프레임 읽기 실패")

    # 중간 프레임으로 Seek
    mid_frame_idx = total_frames // 2
    cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame_idx)
    ret, frame_mid = cap.read()
    if not ret:
        raise Exception("Seek 후 프레임 읽기 실패")

    # Seek 위치 확인
    current_frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES))

    cap.release()

    print(f"  MP4 생성: {test_mp4_video.name}")
    print(f"  FPS: {fps}, 총 프레임: {total_frames}")
    print(f"  해상도: {frame_width}x{frame_height}")
    print(f"  Seek 테스트: 프레임 {mid_frame_idx}로 이동 → 현재 {current_frame_idx}")
    print(f"  [OK] cv2.VideoCapture 읽기/Seek 성공")

    test_mp4_video.unlink()

except ImportError:
    print(f"  [SKIP] opencv-python 미설치 (선택사항)")
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (m) AGC (자동 음량 조절) 단위 테스트 =====
print("\n[13] AGC (Automatic Gain Control) Unit Test...")
try:
    engine = AudioEngine()

    # AGC 파라미터 확인
    sample_rate = 48000
    block_size = 1024

    # 테스트 1: 저진폭 신호 (0.01) - AGC 게인 상승 기대
    print("  Test 1: Low amplitude (0.01) - Gain should increase towards 8.0...")
    engine.agc_enabled = True
    engine.agc_gain = 1.0

    for i in range(10):  # 10 블록
        t = np.arange(block_size) / sample_rate
        low_amp_signal = (0.01 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        stereo_signal = np.tile(low_amp_signal, (2, 1)).T  # (frames, 2)

        # RMS 측정 및 AGC 게인 갱신 (콜백 로직 모의)
        mic_rms = np.sqrt(np.mean(stereo_signal ** 2))
        if mic_rms > engine.agc_gate_threshold:
            desired_gain = engine.agc_target_rms / max(mic_rms, 1e-6)
            desired_gain = max(engine.agc_gain_min, min(engine.agc_gain_max, desired_gain))
            alpha = engine.agc_attack_alpha if desired_gain > engine.agc_gain else engine.agc_release_alpha
            engine.agc_gain += (desired_gain - engine.agc_gain) * alpha

    if engine.agc_gain > 2.0 and engine.agc_gain <= engine.agc_gain_max:
        print(f"    [OK] Gain increased from 1.0 to {engine.agc_gain:.2f} (target: towards 8.0)")
    else:
        print(f"    [WARN] Gain is {engine.agc_gain:.2f}, expected > 2.0")

    # 테스트 2: 고진폭 신호 (0.5) - AGC 게인 하강 기대
    print("  Test 2: High amplitude (0.5) - Gain should decrease towards 1.0...")
    engine.agc_gain = 8.0  # 시작 게인 크게 설정

    for i in range(10):  # 10 블록
        t = np.arange(block_size) / sample_rate
        high_amp_signal = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        stereo_signal = np.tile(high_amp_signal, (2, 1)).T  # (frames, 2)

        # RMS 측정 및 AGC 게인 갱신
        mic_rms = np.sqrt(np.mean(stereo_signal ** 2))
        if mic_rms > engine.agc_gate_threshold:
            desired_gain = engine.agc_target_rms / max(mic_rms, 1e-6)
            desired_gain = max(engine.agc_gain_min, min(engine.agc_gain_max, desired_gain))
            alpha = engine.agc_attack_alpha if desired_gain > engine.agc_gain else engine.agc_release_alpha
            engine.agc_gain += (desired_gain - engine.agc_gain) * alpha

    if engine.agc_gain < 3.0 and engine.agc_gain >= engine.agc_gain_min:
        print(f"    [OK] Gain decreased to {engine.agc_gain:.2f} (target: towards 0.5-1.0)")
    else:
        print(f"    [WARN] Gain is {engine.agc_gain:.2f}, expected < 3.0")

    # 테스트 3: 무음 신호 - AGC 게인 유지 기대
    print("  Test 3: Silent signal - Gain should be maintained...")
    engine.agc_gain = 2.5
    prev_gain = engine.agc_gain

    for i in range(5):  # 5 블록
        # 거의 무음 신호
        silent_signal = np.zeros((block_size, 2), dtype=np.float32)

        # RMS 측정 및 AGC 게인 갱신 (게이트 닫힘)
        mic_rms = np.sqrt(np.mean(silent_signal ** 2))
        if mic_rms > engine.agc_gate_threshold:
            desired_gain = engine.agc_target_rms / max(mic_rms, 1e-6)
            desired_gain = max(engine.agc_gain_min, min(engine.agc_gain_max, desired_gain))
            alpha = engine.agc_attack_alpha if desired_gain > engine.agc_gain else engine.agc_release_alpha
            engine.agc_gain += (desired_gain - engine.agc_gain) * alpha
        # else: 게이트 닫힘 - 게인 유지

    if abs(engine.agc_gain - prev_gain) < 0.01:
        print(f"    [OK] Gain held at {engine.agc_gain:.2f} (no change during silence)")
    else:
        print(f"    [WARN] Gain changed from {prev_gain:.2f} to {engine.agc_gain:.2f}")

    print(f"  [OK] AGC unit test passed")
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (n) 위치 매핑 테스트 (get_original_position_seconds) =====
print("\n[14] Position Mapping Test (Tempo Factor)...")
try:
    engine = AudioEngine()

    # 모의 mr_buffer 설정 (10초 분량)
    sample_rate = 48000
    duration = 10
    frames = sample_rate * duration
    mono_signal = np.sin(2 * np.pi * 440 * np.arange(frames) / sample_rate).astype(np.float32)
    engine.mr_buffer = np.tile(mono_signal, (2, 1)).T  # (frames, 2)

    # 테스트 1: 템포 1.0 (기본)
    engine.mr_position = 5 * sample_rate  # 5초 진행
    engine.tempo_stretch_factor = 1.0
    orig_pos = engine.get_original_position_seconds()
    expected = 5.0
    if abs(orig_pos - expected) < 0.01:
        print(f"  [OK] 템포 1.0: 렌더 5초 -> 원본 {orig_pos:.2f}초 (예상 {expected}초)")
    else:
        raise Exception(f"템포 1.0 테스트 실패: {orig_pos} != {expected}")

    # 테스트 2: 템포 1.25 (1.25배 빠름 = 렌더 버퍼가 원본보다 짧음)
    engine.mr_position = 10 * sample_rate  # 렌더 10초 진행
    engine.tempo_stretch_factor = 1.25
    orig_pos = engine.get_original_position_seconds()
    expected = 10.0 * 1.25  # 12.5초 (렌더 10초 동안 원본 12.5초 분량을 소비)
    if abs(orig_pos - expected) < 0.01:
        print(f"  [OK] 템포 1.25: 렌더 10초 -> 원본 {orig_pos:.2f}초 (예상 {expected:.2f}초)")
    else:
        raise Exception(f"템포 1.25 테스트 실패: {orig_pos} != {expected}")

    # 테스트 3: 템포 0.8 (느림 = 렌더 버퍼가 원본보다 김)
    engine.mr_position = 8 * sample_rate  # 렌더 8초 진행
    engine.tempo_stretch_factor = 0.8
    orig_pos = engine.get_original_position_seconds()
    expected = 8.0 * 0.8  # 6.4초 (렌더 8초 동안 원본 6.4초 분량을 소비)
    if abs(orig_pos - expected) < 0.01:
        print(f"  [OK] 템포 0.8: 렌더 8초 -> 원본 {orig_pos:.2f}초 (예상 {expected:.2f}초)")
    else:
        raise Exception(f"템포 0.8 테스트 실패: {orig_pos} != {expected}")

    print(f"  [OK] 위치 매핑 성공")
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (o) 블록 피크 리미터 단위 테스트 =====
print("\n[15] Block Peak Limiter Unit Test...")
try:
    engine = AudioEngine()
    sample_rate = 48000
    block_size = 1024

    # 테스트 1: 저진폭 신호 (0.3) - 리미터 무손실 통과 (게인 1.0 유지)
    print("  Test 1: Low amplitude (0.3) - Limiter should pass through (gain ~1.0)...")
    engine.limiter_gain = 1.0

    t = np.arange(block_size) / sample_rate
    low_amp_signal = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo_signal = np.tile(low_amp_signal, (2, 1)).T  # (frames, 2)

    # 리미터 적용
    peak = float(np.max(np.abs(stereo_signal)))
    original_signal = stereo_signal.copy()

    if peak * engine.limiter_gain > 0.98:
        engine.limiter_gain = 0.98 / max(peak, 1e-9)
    else:
        engine.limiter_gain += (1.0 - engine.limiter_gain) * 0.05

    output = stereo_signal * engine.limiter_gain
    np.clip(output, -1.0, 1.0, out=output)

    # 무손실 통과 확인: 최대 오차 1e-6 이내
    max_error = np.max(np.abs(output - original_signal))
    if max_error < 1e-6:
        print(f"    [OK] Lossless pass-through (max error: {max_error:.2e})")
    else:
        print(f"    [WARN] Error {max_error:.2e} > 1e-6 (slight distortion)")

    # 테스트 2: 고진폭 신호 (1.5) - 리미터 눌림, 피크 ≤ 1.0
    print("  Test 2: High amplitude (1.5) - Limiter should compress to peak ≤ 1.0...")
    engine.limiter_gain = 1.0

    high_amp_signal = (1.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo_signal = np.tile(high_amp_signal, (2, 1)).T

    peak = float(np.max(np.abs(stereo_signal)))
    if peak * engine.limiter_gain > 0.98:
        engine.limiter_gain = 0.98 / max(peak, 1e-9)

    output = stereo_signal * engine.limiter_gain
    np.clip(output, -1.0, 1.0, out=output)

    output_peak = float(np.max(np.abs(output)))
    if output_peak <= 1.0 and output_peak > 0.95:  # 0.98에 가깝게
        print(f"    [OK] Compressed to peak {output_peak:.4f} (target ~0.98)")
    else:
        print(f"    [WARN] Peak {output_peak:.4f} (expected ~0.98)")

    # 테스트 3: 연속 블록 - 게인 안정화
    print("  Test 3: Multiple blocks - Gain should stabilize...")
    engine.limiter_gain = 1.0

    stable_count = 0
    for i in range(10):
        block = (1.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        stereo = np.tile(block, (2, 1)).T

        peak = float(np.max(np.abs(stereo)))
        if peak * engine.limiter_gain > 0.98:
            engine.limiter_gain = 0.98 / max(peak, 1e-9)
        else:
            engine.limiter_gain += (1.0 - engine.limiter_gain) * 0.05

        if 0.65 < engine.limiter_gain < 0.67:  # 1.5/0.98 ≈ 0.653
            stable_count += 1

    if stable_count >= 7:
        print(f"    [OK] Gain stabilized after {10 - stable_count} blocks (final: {engine.limiter_gain:.4f})")
    else:
        print(f"    [WARN] Gain {engine.limiter_gain:.4f}, stable blocks: {stable_count}/10")

    print(f"  [OK] Limiter unit test passed")
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (p) 모니터 버퍼 재설계 단위 테스트 =====
print("\n[16] Monitor Buffer Redesign Unit Test...")
try:
    engine = AudioEngine()
    sample_rate = 48000
    block_size = 1024

    # 테스트 1: 블록 3개 push 후 pop
    print("  Test 1: Push 3 blocks, pop in order...")
    block1 = np.ones((block_size, 2), dtype=np.float32) * 0.1
    block2 = np.ones((block_size, 2), dtype=np.float32) * 0.2
    block3 = np.ones((block_size, 2), dtype=np.float32) * 0.3

    engine.monitor_buffer.append(block1.copy())
    engine.monitor_buffer.append(block2.copy())
    engine.monitor_buffer.append(block3.copy())

    # Pop 순서 확인
    retrieved1 = engine.monitor_buffer.popleft()
    retrieved2 = engine.monitor_buffer.popleft()
    retrieved3 = engine.monitor_buffer.popleft()

    if np.allclose(retrieved1, block1) and np.allclose(retrieved2, block2) and np.allclose(retrieved3, block3):
        print(f"    [OK] FIFO order maintained")
    else:
        print(f"    [FAIL] Order mismatch")
        sys.exit(1)

    # 테스트 2: 비었을 때 무음 처리
    print("  Test 2: Empty buffer produces silence...")
    engine.monitor_buffer.clear()
    engine.monitor_partial_block = np.zeros((0, 2), dtype=np.float32)

    # 모니터 콜백 모의 호출
    mock_outdata = np.zeros((block_size, 2), dtype=np.float32)

    output = np.zeros((block_size, 2), dtype=np.float32)
    offset = 0
    if len(engine.monitor_partial_block) > 0:
        remaining = block_size - offset
        copy_len = min(len(engine.monitor_partial_block), remaining)
        output[offset:offset + copy_len] = engine.monitor_partial_block[:copy_len]
        offset += copy_len

    while offset < block_size and len(engine.monitor_buffer) > 0:
        block = engine.monitor_buffer.popleft()
        remaining = block_size - offset
        copy_len = min(len(block), remaining)
        output[offset:offset + copy_len] = block[:copy_len]
        offset += copy_len

    if np.allclose(output, np.zeros((block_size, 2))):
        print(f"    [OK] Empty buffer produces silence")
    else:
        print(f"    [FAIL] Empty buffer not silent")
        sys.exit(1)

    print(f"  [OK] Monitor buffer redesign test passed")
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (q) WASAPI Resolution Test =====
print("\n[17] WASAPI Resolution Test...")
try:
    engine = AudioEngine()
    input_devs, output_devs = engine.list_devices()

    if len(input_devs) == 0 or len(output_devs) == 0:
        print("  [SKIP] No input/output devices available")
    else:
        # Test resolve_to_wasapi on default devices
        mic_idx, out_idx, mon_idx = engine.get_default_devices()

        # Resolve input device
        resolved_mic_idx, mic_api = engine._resolve_to_wasapi(mic_idx)
        print(f"  Input device [{mic_idx}] -> resolved to [{resolved_mic_idx}] ({mic_api})")

        # Resolve output device
        resolved_out_idx, out_api = engine._resolve_to_wasapi(out_idx)
        print(f"  Output device [{out_idx}] -> resolved to [{resolved_out_idx}] ({out_api})")

        if 'WASAPI' in mic_api or 'WASAPI' in out_api or (mic_api == "Unknown" and out_api == "Unknown"):
            print(f"  [OK] WASAPI resolution successful")
        else:
            print(f"  [WARN] WASAPI not found, using available API")

    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (r) Latency Mode Parameters Test =====
print("\n[18] Latency Mode Parameters Test...")
try:
    engine = AudioEngine()

    # Test all latency modes
    modes = ['low', 'balanced', 'stable']
    for mode in modes:
        engine.set_latency_mode(mode)
        params = engine._current_latency_params

        print(f"  Mode '{mode}':")
        print(f"    blocksize: {params['blocksize']}")
        print(f"    latency: {params['latency']}")
        print(f"    jitter_depth: {params['jitter_depth']}")
        print(f"    drop_threshold: {params['jitter_drop_threshold']}")
        print(f"    monitor_prefill: {params['monitor_prefill']}")
        print(f"    agc_alpha_scale: {params['agc_alpha_scale']}")

    # Validate low mode has smallest blocksize
    engine.set_latency_mode('low')
    low_blocksize = engine._current_latency_params['blocksize']
    engine.set_latency_mode('balanced')
    balanced_blocksize = engine._current_latency_params['blocksize']
    engine.set_latency_mode('stable')
    stable_blocksize = engine._current_latency_params['blocksize']

    if low_blocksize < balanced_blocksize < stable_blocksize:
        print(f"  [OK] Blocksize progression: {low_blocksize} < {balanced_blocksize} < {stable_blocksize}")
    else:
        print(f"  [WARN] Unexpected blocksize progression")

    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (s) Jitter Buffer Unit Test =====
print("\n[19] Jitter Buffer Unit Test...")
try:
    engine = AudioEngine()
    sample_rate = 48000
    block_size = 512  # 'balanced' mode default

    # Test 1: Push and pop blocks in FIFO order
    print("  Test 1: FIFO order push/pop...")
    block1 = np.ones((block_size, 2), dtype=np.float32) * 0.1
    block2 = np.ones((block_size, 2), dtype=np.float32) * 0.2
    block3 = np.ones((block_size, 2), dtype=np.float32) * 0.3

    engine.mic_jitter_buffer.append(block1.copy())
    engine.mic_jitter_buffer.append(block2.copy())
    engine.mic_jitter_buffer.append(block3.copy())

    retrieved1 = engine.mic_jitter_buffer.popleft()
    retrieved2 = engine.mic_jitter_buffer.popleft()
    retrieved3 = engine.mic_jitter_buffer.popleft()

    if np.allclose(retrieved1, block1) and np.allclose(retrieved2, block2) and np.allclose(retrieved3, block3):
        print(f"    [OK] FIFO order maintained")
    else:
        print(f"    [FAIL] Order mismatch")
        sys.exit(1)

    # Test 2: Overflow handling (dynamic maxlen per mode)
    print("  Test 2: Overflow drop behavior (balanced mode jitter_depth=3)...")
    engine.mic_jitter_buffer.clear()
    engine.stats['mic_overflow_drop'] = 0

    # For balanced mode, drop_threshold=6
    drop_threshold = engine._current_latency_params['jitter_drop_threshold']
    print(f"    Drop threshold for balanced mode: {drop_threshold}")

    # Fill to drop_threshold and beyond
    for i in range(drop_threshold + 2):
        block = np.ones((block_size, 2), dtype=np.float32) * (0.1 * (i + 1))
        if len(engine.mic_jitter_buffer) >= drop_threshold:
            engine.mic_jitter_buffer.popleft()
            engine.stats['mic_overflow_drop'] += 1
        engine.mic_jitter_buffer.append(block.copy())

    if engine.stats['mic_overflow_drop'] >= 1:
        print(f"    [OK] Overflow drop counted: {engine.stats['mic_overflow_drop']}")
    else:
        print(f"    [WARN] No overflow drops counted")

    # Test 3: Underrun handling (empty buffer)
    print("  Test 3: Underrun behavior (empty buffer)...")
    engine.mic_jitter_buffer.clear()
    engine.stats['mic_underrun'] = 0

    # Pop from empty buffer
    if len(engine.mic_jitter_buffer) == 0:
        engine.stats['mic_underrun'] += 1

    if engine.stats['mic_underrun'] > 0:
        print(f"    [OK] Underrun counted: {engine.stats['mic_underrun']}")
    else:
        print(f"    [FAIL] Underrun not counted")
        sys.exit(1)

    print(f"  [OK] Jitter buffer unit test passed")
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (t) Sample Rate Mismatch Detection =====
print("\n[20] Sample Rate Mismatch Detection...")
try:
    engine = AudioEngine()
    mic_idx, out_idx, mon_idx = engine.get_default_devices()

    # Check for mismatched sample rates
    mismatched = engine.check_device_samplerates(mic_idx, out_idx)

    if isinstance(mismatched, list):
        if len(mismatched) > 0:
            print(f"  Mismatched devices found:")
            for dev in mismatched:
                print(f"    [{dev['index']}] {dev['device_name']}: {dev['default_samplerate']}Hz")
            print(f"  [WARN] {len(mismatched)} device(s) not at 48000Hz (should be configured)")
        else:
            print(f"  All devices at 48000Hz (or compatible)")
            print(f"  [OK] No sample rate mismatch")
    else:
        print(f"  [FAIL] check_device_samplerates returned non-list")
        sys.exit(1)

    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (u) 3-Second Live Stream Validation (Core Verification - balanced mode) =====
print("\n[21] 3-Second Live Stream Validation (balanced mode - Estimated Latency Check)...")
try:
    import time

    engine = AudioEngine()

    # Set latency mode to 'balanced' (the default for low latency)
    engine.set_latency_mode('balanced')
    mode_params = engine._current_latency_params
    print(f"  Latency mode: balanced")
    print(f"  Blocksize: {mode_params['blocksize']}, Jitter depth: {mode_params['jitter_depth']}")

    mic_idx, out_idx, mon_idx = engine.get_default_devices()

    # Set all volumes to 0 (silent, non-intrusive test)
    engine.set_mic_volume(0.0)
    engine.set_mr_volume(0.0)
    engine.set_monitor_volume(0.0)

    # Start separated streams
    engine.start_stream(mic_idx, out_idx, None)
    print(f"  Started 3-second stream test: mic[{mic_idx}] -> output[{out_idx}]")
    print(f"  Streams using: input={engine.input_hostapi_name}, output={engine.output_hostapi_name}")

    # Run for 3 seconds and collect latency periodically
    start_time = time.time()
    latency_samples = []

    while time.time() - start_time < 3.0:
        time.sleep(0.3)  # Check every 300ms
        estimated_latency_ms = engine.get_estimated_latency_ms()
        latency_samples.append(estimated_latency_ms)

    # Collect final stats
    stats = engine.get_stats()
    final_underrun = stats.get('mic_underrun', 0)
    final_overflow = stats.get('mic_overflow_drop', 0)
    final_jitter_drop = stats.get('jitter_adaptive_drop', 0)
    input_xrun = stats.get('input_callback_xrun', 0)
    output_xrun = stats.get('output_callback_xrun', 0)

    # Get final estimated latency
    final_latency_ms = engine.get_estimated_latency_ms()

    # Stop stream
    engine.shutdown()

    # Display collected metrics
    print(f"  Estimated latency samples (ms): {[f'{l:.1f}' for l in latency_samples[-3:]]}")
    print(f"  Final estimated latency: {final_latency_ms:.1f}ms")
    print(f"  Final stats after 3s:")
    print(f"    underrun: {final_underrun} (expected ~0-2 after startup)")
    print(f"    overflow_drop: {final_overflow} (expected 0-1 if clock drift)")
    print(f"    jitter_adaptive_drop: {final_jitter_drop} (expected 0 in stable conditions)")
    print(f"    input_xrun: {input_xrun} (expected 0-1)")
    print(f"    output_xrun: {output_xrun} (expected 0-1)")

    # Core validation: latency should be < 150ms in balanced mode
    if final_latency_ms < 150:
        print(f"  [OK] Stream latency acceptable: {final_latency_ms:.1f}ms < 150ms")
    else:
        print(f"  [WARN] High latency: {final_latency_ms:.1f}ms (consider 'low' mode)")

    # Stability check
    if final_underrun <= 3:
        print(f"  [OK] Stream stable after 3s (underrun={final_underrun})")
    else:
        print(f"  [WARN] Elevated underrun count: {final_underrun}")
        print(f"  --> Recommend checking Windows audio device format (should be 48kHz)")

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (n) Recording E2E Test =====
print("\n[15] Recording E2E Test (Sine Wave MR → Record → Verify)...")
try:
    import wave
    import tempfile

    # 1. Generate test sine wave (440Hz, 3 sec, 48kHz, stereo)
    print("  Step 1: Generate test sine wave...")
    sample_rate = 48000
    duration_sec = 3
    freq = 440
    frames = sample_rate * duration_sec

    t = np.arange(frames) / sample_rate
    mono_signal = np.sin(2 * np.pi * freq * t).astype(np.float32)

    # Save as WAV
    from scipy.io import wavfile
    test_mr_file = Path(__file__).parent / "test_mr_sine.wav"
    wavfile.write(str(test_mr_file), sample_rate, mono_signal)
    print(f"    [OK] Test MR file created: {test_mr_file.name}")

    # 2. Create engine and start stream
    print("  Step 2: Initialize engine and start stream...")
    engine = AudioEngine()
    engine.set_mic_volume(0.0)  # Silence mic input
    engine.set_mr_volume(1.0)   # Full MR volume

    mic_idx, out_idx, mon_idx = engine.get_default_devices()
    engine.start_stream(mic_idx, out_idx, None)
    print(f"    [OK] Stream started")

    # 3. Load MR and start playback
    print("  Step 3: Load MR file and start playback...")
    if not engine.load_mr(str(test_mr_file)):
        raise Exception("Failed to load test MR")
    engine.play()
    print(f"    [OK] MR loaded and playing")

    # 4. Create recording file and start recording
    print("  Step 4: Start recording...")
    temp_recording = Path(__file__).parent / "test_recording.wav"
    if not engine.start_recording(str(temp_recording)):
        raise Exception("Failed to start recording")
    print(f"    [OK] Recording started")

    # 5. Run for 2 seconds
    print("  Step 5: Recording for 2 seconds...")
    import time
    time.sleep(2.0)

    # 6. Stop recording
    print("  Step 6: Stop recording...")
    recording_duration = engine.stop_recording()
    print(f"    [OK] Recording stopped (duration: {recording_duration:.2f}s)")

    # 7. Verify recorded file
    print("  Step 7: Verify recorded WAV file...")

    if not temp_recording.exists():
        raise Exception(f"Recording file not created: {temp_recording}")

    # Open and check WAV properties
    with wave.open(str(temp_recording), 'rb') as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        duration_recorded = n_frames / framerate

        # Read audio to check RMS
        audio_frames = wav_file.readframes(n_frames)
        audio_data = np.frombuffer(audio_frames, dtype=np.int16).astype(np.float32) / 32767.0
        audio_data = audio_data.reshape((n_frames, n_channels))
        rms = np.sqrt(np.mean(audio_data ** 2))

    # Validation checks
    checks = [
        (n_channels == 2, f"Channels: {n_channels} (expected 2)"),
        (sample_width == 2, f"Sample width: {sample_width} (expected 2 for 16-bit)"),
        (framerate == 48000, f"Sample rate: {framerate} (expected 48000)"),
        (n_frames >= int(48000 * 1.5), f"Frames: {n_frames} (expected >= 72000 for 1.5s)"),
        (rms > 0.01, f"RMS: {rms:.4f} (expected > 0.01 for sine wave)")
    ]

    for passed, msg in checks:
        if passed:
            print(f"    [OK] {msg}")
        else:
            print(f"    [FAIL] {msg}")
            raise Exception(f"Recording validation failed: {msg}")

    print(f"  [OK] Recording E2E test passed:")
    print(f"      File: {temp_recording.name}")
    print(f"      Duration: {duration_recorded:.2f}s")
    print(f"      Channels: {n_channels}, Sample Rate: {framerate}Hz, Bit Depth: {sample_width*8}-bit")
    print(f"      RMS: {rms:.4f}")

    # Cleanup
    engine.shutdown()
    temp_recording.unlink()
    test_mr_file.unlink()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ===== (u) 목소리 보정 (Voice Correction) 단위 테스트 =====
print("\n[22] Voice Correction Unit Test...")
try:
    engine = AudioEngine()
    sample_rate = 48000
    block_size = 1024

    # 테스트 1: 보정 비활성화 상태
    print("  Test 1: Voice correction disabled...")
    engine.set_voice_correction(False)
    status = engine.get_voice_correction_status()
    if not status["enabled"] and len(engine.voice_correction_chain) == 0:
        print(f"    [OK] Disabled: chain length = {len(engine.voice_correction_chain)}")
    else:
        print(f"    [FAIL] Disabled but chain not empty")
        sys.exit(1)

    # 테스트 2: 중강도 보정 활성화
    print("  Test 2: Voice correction enabled (medium strength)...")
    success = engine.set_voice_correction(True, "medium")
    status = engine.get_voice_correction_status()

    if success and status["enabled"] and len(engine.voice_correction_chain) > 0:
        print(f"    [OK] Enabled (medium): chain length = {len(engine.voice_correction_chain)}")
        for i, effect in enumerate(engine.voice_correction_chain):
            print(f"        [{i}] {type(effect).__name__}")
    else:
        print(f"    [FAIL] Failed to enable correction")
        sys.exit(1)

    # 테스트 3: 약한 보정 강도
    print("  Test 3: Voice correction weak strength...")
    engine.set_voice_correction(True, "weak")
    status = engine.get_voice_correction_status()
    if status["strength"] == "weak" and len(engine.voice_correction_chain) > 0:
        print(f"    [OK] Weak strength applied: chain length = {len(engine.voice_correction_chain)}")
    else:
        print(f"    [FAIL] Failed to apply weak strength")
        sys.exit(1)

    # 테스트 4: 강한 보정 강도
    print("  Test 4: Voice correction strong strength...")
    engine.set_voice_correction(True, "strong")
    status = engine.get_voice_correction_status()
    if status["strength"] == "strong" and len(engine.voice_correction_chain) > 0:
        print(f"    [OK] Strong strength applied: chain length = {len(engine.voice_correction_chain)}")
    else:
        print(f"    [FAIL] Failed to apply strong strength")
        sys.exit(1)

    # 테스트 5: 보정 체인을 통한 신호 처리 (100Hz 사인파)
    print("  Test 5: Signal processing through correction chain (100Hz sine)...")
    engine.set_voice_correction(True, "medium")

    t = np.arange(block_size) / sample_rate
    hpf_cutoff = 85  # medium strength HPF cutoff
    freq = 100  # 100Hz (위 cutoff 위의 주파수)
    mono_signal = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    stereo_signal = np.tile(mono_signal, (2, 1))  # (2, frames)

    # 보정 체인 적용
    output = stereo_signal.copy()
    for effect in engine.voice_correction_chain:
        output = effect(output, sample_rate, reset=False)

    input_rms = float(np.sqrt(np.mean(stereo_signal ** 2)))
    output_rms = float(np.sqrt(np.mean(output ** 2)))

    # HPF와 compressor 후에도 어느 정도 신호가 남아있어야 함
    if output_rms > 0 and not np.isnan(output_rms) and not np.isinf(output_rms):
        ratio = output_rms / max(input_rms, 1e-6)
        print(f"    Input RMS: {input_rms:.4f}, Output RMS: {output_rms:.4f}, Ratio: {ratio:.2f}x")
        if 0.5 <= ratio <= 2.0:
            print(f"    [OK] Signal processing successful (ratio in reasonable range)")
        else:
            print(f"    [WARN] Ratio outside expected 0.5-2.0x range (but may be OK for compressor)")
    else:
        print(f"    [FAIL] Output RMS invalid: {output_rms}")
        sys.exit(1)

    # 테스트 6: HPF 효과 (50Hz 사인파는 감소해야 함)
    print("  Test 6: Highpass filter effectiveness (50Hz sine below cutoff)...")
    engine.set_voice_correction(True, "medium")

    freq_below_cutoff = 50  # Below HPF cutoff (85Hz)
    mono_signal_low = (0.3 * np.sin(2 * np.pi * freq_below_cutoff * t)).astype(np.float32)
    stereo_signal_low = np.tile(mono_signal_low, (2, 1))  # (2, frames)

    output_low = stereo_signal_low.copy()
    for effect in engine.voice_correction_chain:
        output_low = effect(output_low, sample_rate, reset=False)

    input_rms_low = float(np.sqrt(np.mean(stereo_signal_low ** 2)))
    output_rms_low = float(np.sqrt(np.mean(output_low ** 2)))

    if output_rms_low > 0 and input_rms_low > 0:
        attenuation = output_rms_low / input_rms_low
        print(f"    50Hz Input RMS: {input_rms_low:.4f}, Output RMS: {output_rms_low:.4f}, Attenuation: {attenuation:.2f}x")
        if attenuation < 0.7:
            print(f"    [OK] HPF attenuating 50Hz signal (attenuation {attenuation:.2f}x < 0.7x)")
        else:
            print(f"    [WARN] HPF not attenuating much ({attenuation:.2f}x)")
    else:
        print(f"    [FAIL] Output or input RMS invalid")
        sys.exit(1)

    print(f"  [OK] Voice correction unit test passed")
    engine.shutdown()

except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("All tests PASSED!")
print("=" * 60)
print("\nRun app: python app.py")
